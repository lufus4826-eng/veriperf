#!/usr/bin/env python3
"""Deterministic statistical profiling & dual-gate CLI engine for VeriPerf v1.1 (stdlib only).

Subcommands:
  init            Initialize .bughunt/perf-<ts>/ session, snapshot fs-before.txt, worklog.md
  hotspot-scan    AST + pattern scan for O(N^2) loops, N+1 I/O, unbounded reads, loop allocs
  bench-run       Run 3-5 warmups + 15-30 timed iterations; emit <=10-line p50/p95/mem digest
  verify-opt      Enforce Dual Gate (tests + hash, >=15% p50, no p95 regression, mem ceiling, ABBA interleave)
  sandbox         Manage S/sandbox-opt (opt, save, rollback, diff, clean)
  log             Update worklog.md Status line + append timestamped 1-line entries atomically
  summary         Emit Markdown comparison tables from session JSON logs (no raw log reads)
  canonical-hash  Compute deterministic float-rounded / key-sorted SHA-256[:16] digest
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
import ctypes
from dataclasses import dataclass
from datetime import datetime, timezone
import difflib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import statistics
import subprocess
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


IGNORED_SCAN_DIRS = frozenset(
    {
        ".git",
        ".bughunt",
        "node_modules",
        ".venv",
        "venv",
        "env",
        ".env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "target",
        "bin",
        "obj",
        "dist",
        "build",
        ".gradle",
        ".idea",
        ".vs",
        "TestResults",
        "vendor",
        "coverage",
        ".next",
        ".nuxt",
        "out",
    }
)

TEST_DIR_NAMES = frozenset({"tests", "test", "__tests__", "spec", "specs", "benches"})

SANDBOX_ALWAYS_EXCLUDE = frozenset(
    {
        ".git",
        ".bughunt",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".vs",
        "TestResults",
        ".idea",
        ".gradle",
    }
)

SNAPSHOT_FS_SKIP_DIRS = frozenset({".bughunt", ".git", "node_modules"})

EXT_LANG = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".mts": "TypeScript",
    ".cts": "TypeScript",
    ".go": "Go",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".cs": "C#",
    ".php": "PHP",
    ".rs": "Rust",
}

BINARY_EXTS = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".tar",
        ".gz", ".bz2", ".xz", ".7z", ".whl", ".exe", ".dll", ".so", ".dylib",
        ".class", ".jar", ".pyc", ".pyo", ".o", ".a", ".wasm", ".ttf", ".woff",
        ".woff2", ".db", ".sqlite", ".sqlite3",
    }
)

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
METRIC_PREFIX = "VERIPERF_METRIC:"
MIN_WARMUP = 3
MIN_RUNS = 15
HARD_MIN_SPEEDUP_PCT = 15.0
DEFAULT_MAX_MEM_GROWTH_PCT = 25.0
MEM_NOISE_DEADBAND_KB = 16.0


def canonicalize_value(
    val: Any,
    float_digits: int = 6,
    unordered_lists: bool = False,
    sig_figs: Optional[int] = None,
) -> Any:
    """Normalize floats (-0.0 -> 0.0, fixed decimals or sig-figs), sorted dict keys, and optional unordered lists."""
    if isinstance(val, bool) or val is None or isinstance(val, (int, str)):
        return val
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return str(val)
        if val == 0.0:
            return 0.0
        if sig_figs is not None and sig_figs > 0:
            rounded = float(f"{val:.{sig_figs}g}")
        else:
            tiny_threshold = 10.0 ** (-float_digits)
            if 1e-12 <= abs(val) < tiny_threshold and float_digits > 0:
                rounded = float(f"{val:.{float_digits}g}")
            else:
                rounded = round(val, float_digits)
        return 0.0 if rounded == 0.0 else rounded
    if isinstance(val, dict):
        return {
            str(k): canonicalize_value(v, float_digits, unordered_lists, sig_figs)
            for k, v in sorted(val.items(), key=lambda kv: str(kv[0]))
        }
    if isinstance(val, (set, frozenset)):
        items = [canonicalize_value(x, float_digits, unordered_lists, sig_figs) for x in val]
        items.sort(key=lambda x: json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return items
    if isinstance(val, (list, tuple)):
        items = [canonicalize_value(x, float_digits, unordered_lists, sig_figs) for x in val]
        if unordered_lists:
            items.sort(key=lambda x: json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return items
    return str(val)


def canonical_hash(
    val: Any,
    float_digits: int = 6,
    unordered_lists: bool = False,
    sig_figs: Optional[int] = None,
) -> str:
    """Return deterministic 16-char SHA-256 hex digest tolerant of float epsilon and key order."""
    canon = canonicalize_value(
        val,
        float_digits=float_digits,
        unordered_lists=unordered_lists,
        sig_figs=sig_figs,
    )
    encoded = json.dumps(canon, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def cmd_canonical_hash(args: argparse.Namespace) -> int:
    if args.sig_figs is not None and int(args.sig_figs) <= 0:
        raise SystemExit("[vp.py] ERROR: --sig-figs must be >= 1")
    if args.json_file:
        fp = Path(args.json_file)
        if not fp.is_file():
            raise SystemExit(f"[vp.py] ERROR: JSON file not found: {fp}")
        raw = fp.read_text(encoding="utf-8")
    elif args.json_str is not None and args.json_str != "-":
        raw = args.json_str
    else:
        raw = sys.stdin.read()
    try:
        val = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[vp.py] ERROR: invalid JSON input ({exc})")
    print(
        canonical_hash(
            val,
            float_digits=int(args.float_digits),
            unordered_lists=bool(args.unordered_lists),
            sig_figs=int(args.sig_figs) if args.sig_figs is not None else None,
        )
    )
    return 0


# ---------------------------------------------------------------------------
# General Utilities & Isolation
# ---------------------------------------------------------------------------


def configure_stdio() -> None:
    """Ensure UTF-8 stdout/stderr on Windows without failing on redirected streams."""
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def trunc(text: str, max_len: int = 120) -> str:
    clean = " ".join(text.strip().split())
    if len(clean) <= max_len:
        return clean
    return clean[: max(0, max_len - 3)] + "..."


def sanitize_id(raw_id: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_id.strip())
    if not clean or clean in (".", ".."):
        raise SystemExit(f"[vp.py] ERROR: Invalid target ID: {raw_id!r}")
    return clean


def resolve_root(root_arg: Optional[str]) -> Path:
    root = Path(root_arg).resolve() if root_arg else Path.cwd().resolve()
    if not root.exists():
        raise SystemExit(f"[vp.py] ERROR: ROOT does not exist: {root}")
    return root


def resolve_session(s_arg: str, root_arg: Optional[str] = None) -> Tuple[Path, Path, str, Path]:
    """Resolve (ROOT, S_DIR, SNAME, CACHE_DIR) from --s argument."""
    s_path = Path(s_arg)
    if not s_path.is_absolute():
        base = resolve_root(root_arg)
        s_path = (base / s_path).resolve()
    else:
        s_path = s_path.resolve()

    if root_arg:
        root = resolve_root(root_arg)
    elif s_path.parent.name == ".bughunt":
        root = s_path.parent.parent.resolve()
    else:
        root = Path.cwd().resolve()

    cache_dir = root / ".bughunt" / ".cache"
    return root, s_path, s_path.name, cache_dir


def find_latest_session(root: Path) -> Optional[Path]:
    bh_dir = root / ".bughunt"
    if not bh_dir.is_dir():
        return None
    sessions = [
        d for d in bh_dir.iterdir()
        if d.is_dir() and d.name.startswith("perf-")
    ]
    if not sessions:
        return None
    sessions.sort(key=lambda d: d.name, reverse=True)
    return sessions[0]


def rel_posix(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def build_isolated_env(
    cache_dir: Path,
    target_root: Optional[Path] = None,
    extra_env: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Build deterministic isolated environment with cache redirection and offline flags."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "MYPY_CACHE_DIR": str(cache_dir / "mypy"),
            "RUFF_CACHE_DIR": str(cache_dir / "ruff"),
            "GOPROXY": "off",
            "GOCACHE": str(cache_dir / "go-build"),
            "GOLANGCI_LINT_CACHE": str(cache_dir / "golangci-lint"),
            "CARGO_TARGET_DIR": str(cache_dir / "cargo-target"),
            "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
            "DOTNET_NOLOGO": "1",
            "npm_config_offline": "true",
            "npm_config_cache": str(cache_dir / "npm"),
            "NO_COLOR": "1",
            "FORCE_COLOR": "0",
            "TERM": "dumb",
        }
    )
    if target_root is not None:
        t_str = str(target_root.resolve())
        env["VERIPERF_TARGET_ROOT"] = t_str
        prev_py = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{t_str}{os.pathsep}{prev_py}" if prev_py else t_str
        prev_node = env.get("NODE_PATH", "")
        nm = str((target_root / "node_modules").resolve())
        env["NODE_PATH"] = f"{nm}{os.pathsep}{prev_node}" if prev_node else nm
    if extra_env:
        env.update(extra_env)
    return env


def _split_cli_args(cmd_str: str) -> List[str]:
    """Split a command string preserving Windows backslashes and quotes."""
    if os.name != "nt":
        return shlex.split(cmd_str, posix=True)
    tokens: List[str] = []
    buf: List[str] = []
    quote: Optional[str] = None
    in_token = False
    i = 0
    n = len(cmd_str)
    while i < n:
        ch = cmd_str[i]
        if quote is not None:
            if ch == "\\" and quote == '"' and i + 1 < n and cmd_str[i + 1] == '"':
                buf.append('"')
                i += 2
                continue
            if ch == quote:
                quote = None
                i += 1
                continue
            buf.append(ch)
        else:
            if ch in (" ", "\t", "\r", "\n"):
                if in_token:
                    tokens.append("".join(buf))
                    buf.clear()
                    in_token = False
                i += 1
                continue
            in_token = True
            if ch in ('"', "'"):
                quote = ch
            else:
                buf.append(ch)
        i += 1
    if in_token:
        tokens.append("".join(buf))
    return tokens


def parse_cmd_tokens(raw_cmd: Sequence[str] | str) -> List[str]:
    if isinstance(raw_cmd, str):
        return _split_cli_args(raw_cmd)
    cmd = list(raw_cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if len(cmd) == 1 and " " in cmd[0] and not Path(cmd[0]).exists() and not shutil.which(cmd[0]):
        cmd = _split_cli_args(cmd[0])
    return cmd


def _resolve_bughunt_cmd_paths(cmd_tokens: List[str], root: Path) -> List[str]:
    """Resolve relative .bughunt/... paths against ROOT so commands work even when cwd=sandbox-opt."""
    resolved: List[str] = []
    for tok in cmd_tokens:
        norm = tok.replace("\\", "/")
        if norm.startswith(".bughunt/") or norm == ".bughunt":
            cand = (root / tok).resolve()
            if cand.exists():
                resolved.append(str(cand))
                continue
        resolved.append(tok)
    return resolved


def resolve_executable(cmd: List[str], cwd: Path, env: Dict[str, str]) -> List[str]:
    """Resolve Windows .cmd/.bat/.exe wrappers or Python executable safely."""
    if not cmd:
        return cmd
    exe = cmd[0]
    if exe in ("python", "python3") and os.name == "nt":
        return [sys.executable] + cmd[1:]
    if "/" in exe or "\\" in exe or exe.startswith("."):
        cand = cwd / exe
        if cand.is_file():
            return [str(cand)] + cmd[1:]
        if os.name == "nt" and not cand.suffix:
            for ext in (".bat", ".cmd", ".exe"):
                alt = cwd / (exe + ext)
                if alt.is_file():
                    return [str(alt)] + cmd[1:]
        return cmd
    resolved = shutil.which(exe, path=env.get("PATH"))
    if resolved:
        return [resolved] + cmd[1:]
    return cmd


# ---------------------------------------------------------------------------
# Snapshots & Cleanliness
# ---------------------------------------------------------------------------


def is_git_repo(root: Path) -> bool:
    if not shutil.which("git"):
        return False
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        return proc.returncode == 0 and proc.stdout.strip().lower() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


def take_snapshot(root: Path, forced_mode: Optional[str] = None) -> Tuple[str, List[str]]:
    use_git = forced_mode == "git" or (forced_mode is None and is_git_repo(root))
    if use_git:
        try:
            proc = subprocess.run(
                ["git", "-c", "core.quotepath=false", "-C", str(root),
                 "status", "--porcelain", "--ignored", "--", "."],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            if proc.returncode == 0:
                lines = [ln.rstrip("\r\n") for ln in proc.stdout.splitlines() if ln.strip()]
                return "git", lines
        except (OSError, subprocess.SubprocessError):
            pass

    entries: List[str] = []
    root_res = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root_res):
        dirnames[:] = sorted(d for d in dirnames if d not in SNAPSHOT_FS_SKIP_DIRS)
        dp = Path(dirpath)
        for fname in sorted(filenames):
            fp = dp / fname
            try:
                st = fp.lstat()
            except OSError:
                continue
            rel = fp.relative_to(root_res).as_posix()
            mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
            entries.append(f"{rel}\t{mtime}")
    entries.sort()
    return "fs", entries


def _is_bughunt_git_line(line: str) -> bool:
    path_part = line[3:].strip() if len(line) > 3 else line.strip()
    if " -> " in path_part:
        path_part = path_part.split(" -> ")[-1].strip()
    path_part = path_part.strip('"').replace("\\", "/")
    parts = [p for p in path_part.split("/") if p]
    return ".bughunt" in parts


def diff_snapshots(
    before_lines: List[str], after_lines: List[str], mode: str
) -> Dict[str, List[str]]:
    if mode == "git":
        b_filtered = [ln for ln in before_lines if not _is_bughunt_git_line(ln)]
        a_filtered = [ln for ln in after_lines if not _is_bughunt_git_line(ln)]
        b_set, a_set = set(b_filtered), set(a_filtered)
        return {
            "new_or_changed": [ln for ln in a_filtered if ln not in b_set],
            "resolved": [ln for ln in b_filtered if ln not in a_set],
        }

    def parse_fs(lines: Iterable[str]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for ln in lines:
            if "\t" in ln:
                p, ts = ln.split("\t", 1)
                if not p.startswith(".bughunt/"):
                    out[p] = ts
        return out

    b_map = parse_fs(before_lines)
    a_map = parse_fs(after_lines)
    return {
        "added": [p for p in sorted(a_map) if p not in b_map],
        "modified": [p for p in sorted(a_map) if p in b_map and a_map[p] != b_map[p]],
        "removed": [p for p in sorted(b_map) if p not in a_map],
    }


def _remove_readonly_and_rmtree(path: Path) -> None:
    def _onerror(func: Any, p: str, _exc: Any) -> None:
        try:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            func(p)
        except OSError:
            pass

    if path.exists():
        shutil.rmtree(path, onerror=_onerror)


def _clean_new_agent_caches(root: Path, diff: Dict[str, List[str]], snap_mode: str) -> List[str]:
    removed: List[str] = []
    if snap_mode == "git":
        candidates = []
        for ln in diff.get("new_or_changed", []):
            if len(ln) > 3 and ln[:2] in ("??", "!!"):
                candidates.append(ln[3:].strip().strip('"').replace("\\", "/"))
    else:
        candidates = diff.get("added", [])

    for rel_s in candidates:
        parts = [p for p in rel_s.split("/") if p]
        if ".bughunt" in parts:
            continue
        cache_idx = next((i for i, p in enumerate(parts) if p in ("__pycache__", ".pytest_cache")), None)
        if cache_idx is None:
            continue
        target = root / Path(*parts[: cache_idx + 1])
        if target.exists():
            if target.is_dir():
                _remove_readonly_and_rmtree(target)
            else:
                target.unlink(missing_ok=True)
            removed.append(rel_posix(target, root))
    return sorted(set(removed))


# ---------------------------------------------------------------------------
# Subcommand: init & log
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    bh_root = root / ".bughunt"
    bh_root.mkdir(parents=True, exist_ok=True)
    cache_dir = bh_root / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    sname = f"perf-{stamp}"
    s_dir = bh_root / sname
    suffix = 1
    while s_dir.exists():
        sname = f"perf-{stamp}-{suffix}"
        s_dir = bh_root / sname
        suffix += 1

    for sub in ("benches", "logs", "tmp"):
        (s_dir / sub).mkdir(parents=True, exist_ok=True)

    snap_mode, snap_lines = take_snapshot(root)
    (s_dir / "logs" / "fs-before.txt").write_text(
        ("\n".join(snap_lines) + "\n") if snap_lines else "", encoding="utf-8"
    )
    (s_dir / "logs" / ".snapshot-mode").write_text(snap_mode + "\n", encoding="utf-8")

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    worklog = s_dir / "worklog.md"
    worklog.write_text(
        f"Status: Initialized\n\n# VeriPerf Worklog\n- Session: `{sname}` | Root: `{root}` | Started: `{now_iso}`\n",
        encoding="utf-8",
    )

    payload = {
        "S": rel_posix(s_dir, root),
        "SNAME": sname,
        "CACHE": rel_posix(cache_dir, root),
        "ROOT": str(root),
        "snapshot": snap_mode,
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    _, s_dir, _, _ = resolve_session(args.s, args.root)
    worklog = s_dir / "worklog.md"
    if not s_dir.is_dir():
        raise SystemExit(f"[vp.py] ERROR: Session directory missing: {s_dir}")

    lines = worklog.read_text(encoding="utf-8", errors="replace").splitlines() if worklog.is_file() else []
    if not lines:
        lines = ["Status: Initialized", "", "# VeriPerf Worklog"]

    if args.status:
        new_status = f"Status: {' '.join(args.status.strip().splitlines())}"
        if lines[0].startswith("Status:"):
            lines[0] = new_status
        else:
            lines.insert(0, new_status)

    entries: List[str] = []
    if args.entry:
        for raw in args.entry:
            if raw == "-":
                entries.extend(ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip())
            elif raw.strip():
                entries.append(" ".join(raw.strip().splitlines()))
    if args.entry_file:
        ef = Path(args.entry_file)
        if ef.is_file():
            entries.extend(ln.strip() for ln in ef.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip())

    ts = datetime.now().strftime("%H:%M:%S")
    for ent in entries:
        prefix = "" if ent.startswith("-") else "- "
        lines.append(f"{prefix}[{ts}] {ent}")

    worklog.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[vp log] status={lines[0].removeprefix('Status: ').strip()!r} entries_added={len(entries)}")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: hotspot-scan (Python AST + Multi-Lang Pattern Scanner)
# ---------------------------------------------------------------------------


@dataclass
class HotspotFinding:
    file: str
    line: int
    symbol: str
    category: str
    score: int
    evidence: str

    @property
    def severity(self) -> str:
        if self.score >= 80:
            return "HIGH"
        if self.score >= 50:
            return "MEDIUM"
        return "LOW"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "score": self.score,
            "category": self.category,
            "file": self.file,
            "line": self.line,
            "symbol": self.symbol,
            "evidence": self.evidence,
        }


def _is_test_relpath(rel_path: str) -> bool:
    parts = [p.lower() for p in rel_path.replace("\\", "/").split("/")]
    if any(p in TEST_DIR_NAMES for p in parts[:-1]):
        return True
    fname = parts[-1] if parts else ""
    return bool(
        fname.startswith("test_")
        or "_test." in fname
        or ".test." in fname
        or ".spec." in fname
        or fname.endswith(("tests.cs", "test.cs", "test.java", "test.kt", "test.php"))
    )


def _python_call_name(node: ast.Call) -> str:
    fn = node.func
    parts: List[str] = []
    cur: Any = fn
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    parts.reverse()
    return ".".join(parts)


class _PythonHotspotVisitor(ast.NodeVisitor):
    """AST visitor tracking loop nesting depth and enclosing symbol for Python code."""

    IO_EXACT_FUNCS = frozenset({"open", "sleep"})
    IO_EXACT_METHODS = frozenset(
        {
            "execute", "executemany", "fetchone", "fetchall", "fetchmany",
            "query", "filter", "filter_by", "find_one", "find",
            "urlopen", "urlretrieve", "read_text", "read_bytes", "commit",
            "flush", "sleep",
        }
    )
    IO_RECEIVER_HINTS = frozenset(
        {"requests", "httpx", "aiohttp", "urllib", "cursor", "cur", "db", "conn",
         "session", "prisma", "orm", "repo", "repository", "client", "http", "api", "store"}
    )
    IO_RECEIVER_METHODS = frozenset({"get", "post", "put", "patch", "delete", "fetch", "all", "scalar", "request"})
    RE_FUNCS = frozenset({"re.compile", "re.match", "re.search", "re.findall", "re.finditer", "re.sub", "re.split"})
    ALLOC_FUNCS = frozenset({"json.loads", "json.dumps", "copy.deepcopy", "sorted"})
    STR_NAME_HINTS = ("str", "text", "msg", "log", "line", "buf", "out", "html", "xml", "csv", "acc", "body", "sql")

    def __init__(self, rel_path: str, src_lines: List[str]) -> None:
        self.rel_path = rel_path
        self.src_lines = src_lines
        self.findings: List[HotspotFinding] = []
        self.scope_stack: List[str] = []
        self.loop_depth = 0
        self._seen: set[Tuple[int, str]] = set()

    def _symbol(self) -> str:
        return ".".join(self.scope_stack) if self.scope_stack else "<module>"

    def _add(self, line: int, category: str, score: int, evidence: str) -> None:
        key = (line, category)
        if key in self._seen:
            return
        self._seen.add(key)
        self.findings.append(
            HotspotFinding(
                file=self.rel_path,
                line=line,
                symbol=self._symbol(),
                category=category,
                score=score,
                evidence=trunc(evidence, 90),
            )
        )

    def _src_line(self, lineno: int) -> str:
        if 1 <= lineno <= len(self.src_lines):
            return self.src_lines[lineno - 1].strip()
        return ""

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope_stack.append(node.name)
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope_stack.append(node.name)
        saved_depth = self.loop_depth
        self.loop_depth = 0
        self.generic_visit(node)
        self.loop_depth = saved_depth
        self.scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.scope_stack.append(node.name)
        saved_depth = self.loop_depth
        self.loop_depth = 0
        self.generic_visit(node)
        self.loop_depth = saved_depth
        self.scope_stack.pop()

    def _visit_loop(self, node: ast.AST, kind: str) -> None:
        if self.loop_depth >= 1:
            self._add(
                getattr(node, "lineno", 1),
                "NESTED_LOOP",
                88 if kind in ("for", "async for", "while") else 82,
                f"Nested {kind} at depth={self.loop_depth + 1}: {self._src_line(getattr(node, 'lineno', 1))}",
            )
        self.loop_depth += 1
        self.generic_visit(node)
        self.loop_depth -= 1

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node, "for")

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node, "async for")

    def visit_While(self, node: ast.While) -> None:
        self._visit_loop(node, "while")

    def _visit_comp(self, node: Any, kind: str) -> None:
        gens = len(getattr(node, "generators", []))
        if self.loop_depth >= 1:
            self._add(node.lineno, "NESTED_LOOP", 82, f"{kind} inside loop: {self._src_line(node.lineno)}")
        elif gens >= 2:
            self._add(node.lineno, "NESTED_LOOP", 82, f"Multi-clause {kind} ({gens} loops): {self._src_line(node.lineno)}")
        self.loop_depth += max(1, gens)
        self.generic_visit(node)
        self.loop_depth -= max(1, gens)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comp(node, "listcomp")

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comp(node, "setcomp")

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comp(node, "dictcomp")

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comp(node, "genexpr")

    def visit_Compare(self, node: ast.Compare) -> None:
        if self.loop_depth >= 1:
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(op, (ast.In, ast.NotIn)) and isinstance(comp, (ast.Name, ast.Attribute, ast.Subscript)):
                    # Skip small constant literal collections
                    self._add(
                        node.lineno,
                        "NESTED_LOOP",
                        76,
                        f"Membership scan `{self._src_line(node.lineno)}` inside loop (O(N) if list/seq)",
                    )
                    break
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if self.loop_depth >= 1 and isinstance(node.op, ast.Add):
            tname = node.target.id.lower() if isinstance(node.target, ast.Name) else ""
            is_str_rhs = (
                isinstance(node.value, ast.JoinedStr)
                or (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str))
                or (isinstance(node.value, ast.BinOp) and isinstance(node.value.op, ast.Mod))
            )
            if is_str_rhs or any(h in tname for h in self.STR_NAME_HINTS):
                self._add(
                    node.lineno,
                    "LOOP_ALLOC_OR_RECOMPILE",
                    78,
                    f"String `+=` inside loop: {self._src_line(node.lineno)}",
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        full_name = _python_call_name(node)
        parts = full_name.split(".") if full_name else []
        method = parts[-1] if parts else ""
        receiver = parts[-2].lower() if len(parts) >= 2 else ""

        # Unbounded memory reads (any depth; higher if inside loop)
        is_unbounded = False
        if isinstance(node.func, ast.Attribute):
            if method == "read" and not node.args and not node.keywords:
                is_unbounded = True
            elif method in ("readlines", "read_text", "read_bytes", "fetchall"):
                is_unbounded = True
            elif full_name.endswith("read_csv") and not any(kw.arg == "chunksize" for kw in node.keywords):
                is_unbounded = True
        if is_unbounded:
            self._add(
                node.lineno,
                "UNBOUNDED_MEMORY_READ",
                85 if self.loop_depth >= 1 else 74,
                f"Unbounded memory load `{full_name}()`: {self._src_line(node.lineno)}",
            )

        # Loop-level bottlenecks
        if self.loop_depth >= 1:
            if (
                full_name in self.IO_EXACT_FUNCS
                or method in self.IO_EXACT_METHODS
                or (receiver in self.IO_RECEIVER_HINTS and method in self.IO_RECEIVER_METHODS)
            ):
                self._add(
                    node.lineno,
                    "N_PLUS_ONE_IO",
                    92,
                    f"I/O or DB call `{full_name}()` inside loop: {self._src_line(node.lineno)}",
                )
            elif full_name in self.RE_FUNCS:
                self._add(
                    node.lineno,
                    "LOOP_ALLOC_OR_RECOMPILE",
                    74,
                    f"Regex `{full_name}()` inside loop: {self._src_line(node.lineno)}",
                )
            elif full_name in self.ALLOC_FUNCS or method == "sort":
                self._add(
                    node.lineno,
                    "LOOP_ALLOC_OR_RECOMPILE",
                    72,
                    f"Repeated `{full_name}()` inside loop: {self._src_line(node.lineno)}",
                )
            elif method in ("index", "count", "remove") and isinstance(node.func, ast.Attribute):
                self._add(
                    node.lineno,
                    "NESTED_LOOP",
                    80,
                    f"Linear `.{method}()` scan inside loop: {self._src_line(node.lineno)}",
                )
        self.generic_visit(node)


def _scan_python_file(fp: Path, rel_path: str) -> Tuple[int, List[HotspotFinding]]:
    try:
        text = fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0, []
    lines = text.splitlines()
    try:
        tree = ast.parse(text, filename=rel_path)
    except SyntaxError:
        return len(lines), []
    visitor = _PythonHotspotVisitor(rel_path, lines)
    visitor.visit(tree)
    return len(lines), visitor.findings


_BRACE_FN_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:public|private|protected|internal|static|pub|fn|func|function)?\s*"
    r"(?:fn|func|function)?\s*([A-Za-z_][A-Za-z0-9_]*)\s*\("
)
_BRACE_LOOP_RE = re.compile(
    r"\b(?:for|while)\s*\(|\bfor\s+[A-Za-z0-9_,*\s]+:=\s*range\b|\bloop\s*\{|\.(?:forEach|map|filter|flatMap|reduce)\s*\("
)
_BRACE_IO_RE = re.compile(
    r"\b(?:db\.Query|db\.Exec|prisma\.|repository\.|jdbcTemplate\.|EntityManager|DbContext|SaveChanges|"
    r"fetch\s*\(|axios\.|http\.Get|reqwest::|fs\.readFileSync|os\.ReadFile|Files\.readAllBytes|"
    r"File\.ReadAllText|fs::read_to_string|Thread\.sleep|thread::sleep)\b"
)
_BRACE_UNBOUNDED_RE = re.compile(
    r"\b(?:fs\.readFileSync|os\.ReadFile|io\.ReadAll|ioutil\.ReadAll|Files\.readAllBytes|"
    r"Files\.readString|File\.ReadAllText|fs::read_to_string|file_get_contents)\b"
)
_BRACE_ALLOC_RE = re.compile(
    r"\b(?:new\s+RegExp|regexp\.MustCompile|Pattern\.compile|new\s+Regex|Regex::new|"
    r"JSON\.stringify|JSON\.parse|json\.Marshal|json\.Unmarshal|serde_json::)\b"
)
_BRACE_SCAN_RE = re.compile(r"\.(?:includes|indexOf|find|contains|Contains)\s*\(|\bslices\.Contains\s*\(")


def _scan_brace_file(fp: Path, rel_path: str) -> Tuple[int, List[HotspotFinding]]:
    try:
        lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0, []

    findings: List[HotspotFinding] = []
    brace_depth = 0
    loop_depths: List[int] = []
    cur_sym = "<file>"
    sym_depth = 0

    for lineno, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith(("//", "/*", "*", "#")):
            continue

        m_fn = _BRACE_FN_RE.match(stripped)
        if m_fn and m_fn.group(1) not in ("if", "for", "while", "switch", "catch"):
            cur_sym = m_fn.group(1)
            sym_depth = brace_depth

        opened_loop = bool(_BRACE_LOOP_RE.search(stripped))
        if opened_loop:
            if loop_depths:
                findings.append(
                    HotspotFinding(
                        file=rel_path,
                        line=lineno,
                        symbol=cur_sym,
                        category="NESTED_LOOP",
                        score=86,
                        evidence=f"Nested loop (depth={len(loop_depths) + 1}): {trunc(stripped, 80)}",
                    )
                )
            loop_depths.append(brace_depth + max(1, stripped.count("{") - stripped.count("}")))

        in_loop = bool(loop_depths)
        if _BRACE_UNBOUNDED_RE.search(stripped):
            findings.append(
                HotspotFinding(
                    file=rel_path,
                    line=lineno,
                    symbol=cur_sym,
                    category="UNBOUNDED_MEMORY_READ",
                    score=84 if in_loop else 74,
                    evidence=f"Whole-file memory read: {trunc(stripped, 80)}",
                )
            )
        if in_loop:
            if _BRACE_IO_RE.search(stripped):
                findings.append(
                    HotspotFinding(
                        file=rel_path,
                        line=lineno,
                        symbol=cur_sym,
                        category="N_PLUS_ONE_IO",
                        score=90,
                        evidence=f"I/O or DB call inside loop: {trunc(stripped, 80)}",
                    )
                )
            elif _BRACE_SCAN_RE.search(stripped):
                findings.append(
                    HotspotFinding(
                        file=rel_path,
                        line=lineno,
                        symbol=cur_sym,
                        category="NESTED_LOOP",
                        score=76,
                        evidence=f"Linear collection scan in loop: {trunc(stripped, 80)}",
                    )
                )
            elif _BRACE_ALLOC_RE.search(stripped):
                findings.append(
                    HotspotFinding(
                        file=rel_path,
                        line=lineno,
                        symbol=cur_sym,
                        category="LOOP_ALLOC_OR_RECOMPILE",
                        score=74,
                        evidence=f"Alloc/regex/JSON op in loop: {trunc(stripped, 80)}",
                    )
                )

        brace_depth = max(0, brace_depth + stripped.count("{") - stripped.count("}"))
        while loop_depths and brace_depth < loop_depths[-1]:
            loop_depths.pop()
        if brace_depth <= sym_depth:
            cur_sym = "<file>"

    return len(lines), findings


def cmd_hotspot_scan(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    scan_root = (root / args.scope).resolve() if args.scope else root
    if not scan_root.exists():
        raise SystemExit(f"[vp.py] ERROR: Scan scope not found: {scan_root}")

    lang_counts: Counter[str] = Counter()
    total_loc = 0
    all_findings: List[HotspotFinding] = []

    for dirpath, dirnames, filenames in os.walk(scan_root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_SCAN_DIRS)
        dp = Path(dirpath)
        for fname in sorted(filenames):
            fp = dp / fname
            ext = fp.suffix.lower()
            lang = EXT_LANG.get(ext)
            if not lang:
                continue
            rel = rel_posix(fp, root)
            if _is_test_relpath(rel):
                continue
            lang_counts[lang] += 1
            if ext == ".py":
                loc, fnds = _scan_python_file(fp, rel)
            else:
                loc, fnds = _scan_brace_file(fp, rel)
            total_loc += loc
            all_findings.extend(fnds)

    all_findings.sort(key=lambda f: (-f.score, f.file, f.line, f.category))
    dict_findings = [f.to_dict() for f in all_findings]

    s_dir: Optional[Path] = None
    if getattr(args, "s", None):
        _, s_dir, _, _ = resolve_session(args.s, args.root)
    else:
        s_dir = find_latest_session(root)

    if s_dir and (s_dir / "logs").is_dir():
        scan_out = {
            "root": str(root),
            "scope": rel_posix(scan_root, root),
            "languages": dict(lang_counts),
            "total_loc": total_loc,
            "hotspot_count": len(dict_findings),
            "hotspots": dict_findings,
        }
        (s_dir / "logs" / "hotspot-scan.json").write_text(
            json.dumps(scan_out, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    if args.json:
        print(
            json.dumps(
                {
                    "languages": dict(lang_counts),
                    "total_loc": total_loc,
                    "hotspot_count": len(dict_findings),
                    "hotspots": dict_findings[:25],
                },
                ensure_ascii=False,
            )
        )
        return 0

    max_lines = max(5, min(35, int(args.max_lines)))
    lang_str = ", ".join(f"{k}:{v}" for k, v in lang_counts.most_common()) or "none"
    out_lines = [
        f"[vp hotspot-scan] root={root.name} loc={total_loc} langs=[{lang_str}] hotspots={len(dict_findings)}"
    ]
    if not dict_findings:
        out_lines.append("No static hotspot patterns detected in non-test files.")
    else:
        out_lines.append("Rank | Sev    | Category                | Location | Evidence")
        reserved_footer = 1 if len(dict_findings) > max_lines - 3 else 0
        room = max(1, max_lines - len(out_lines) - reserved_footer)
        for idx, f in enumerate(all_findings[:room], start=1):
            loc_str = f"{f.file}:{f.line} ({f.symbol})"
            out_lines.append(
                f"#{idx:<3} | {f.severity:<6} | {f.category:<23} | {trunc(loc_str, 36)} | {trunc(f.evidence, 60)}"
            )
        if len(dict_findings) > room:
            out_lines.append(f"... (+{len(dict_findings) - room} more in hotspot-scan.json)")

    print("\n".join(out_lines[:max_lines]))
    return 0


# ---------------------------------------------------------------------------
# Statistical Benchmarking Helpers (bench-run)
# ---------------------------------------------------------------------------


def _get_windows_peak_rss_kb(handle_int: int) -> float:
    """Query PeakWorkingSetSize via K32GetProcessMemoryInfo / psapi on Windows."""
    if os.name != "nt" or not handle_int:
        return 0.0

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_uint32),
            ("PageFaultCount", ctypes.c_uint32),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    try:
        pmc = PROCESS_MEMORY_COUNTERS()
        pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        ok = k32.K32GetProcessMemoryInfo(ctypes.c_void_p(handle_int), ctypes.byref(pmc), pmc.cb)
        if not ok:
            psapi = ctypes.windll.psapi  # type: ignore[attr-defined]
            ok = psapi.GetProcessMemoryInfo(ctypes.c_void_p(handle_int), ctypes.byref(pmc), pmc.cb)
        if ok and pmc.PeakWorkingSetSize > 0:
            return round(pmc.PeakWorkingSetSize / 1024.0, 2)
    except Exception:
        pass
    return 0.0


def _compute_tier_stats(
    tier: str,
    warmup: int,
    samples_ms: List[float],
    peak_kb: float,
    result_hash: str,
) -> Dict[str, Any]:
    if not samples_ms:
        raise ValueError(f"Tier {tier!r} produced 0 samples")
    ordered = sorted(samples_ms)
    n = len(ordered)
    p50 = statistics.median(ordered)
    p95_idx = max(0, min(n - 1, math.ceil(0.95 * n) - 1))
    p95 = ordered[p95_idx]
    mean = statistics.mean(ordered)
    stdev = statistics.stdev(ordered) if n > 1 else 0.0
    cv_pct = (stdev / mean * 100.0) if mean > 0 else 0.0
    return {
        "tier": tier,
        "warmup": warmup,
        "runs": n,
        "p50_ms": round(p50, 4),
        "p95_ms": round(p95, 4),
        "mean_ms": round(mean, 4),
        "stdev_ms": round(stdev, 4),
        "cv_pct": round(cv_pct, 2),
        "min_ms": round(ordered[0], 4),
        "max_ms": round(ordered[-1], 4),
        "peak_kb": round(float(peak_kb), 2),
        "result_hash": str(result_hash),
    }


def _select_primary_tier(tiers: Dict[str, Dict[str, Any]], preferred: str = "large") -> str:
    if preferred in tiers:
        return preferred
    for cand in ("large", "medium", "small", "default"):
        if cand in tiers:
            return cand
    return max(tiers.keys(), key=lambda t: tiers[t]["p50_ms"])


def _parse_metric_lines(
    log_text: str,
    cli_warmup: int,
    min_runs: int = MIN_RUNS,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[float]], Optional[str]]:
    """Parse VERIPERF_METRIC:{...} lines into tier stats and raw samples_ms."""
    tiers: Dict[str, Dict[str, Any]] = {}
    raw_samples: Dict[str, List[float]] = {}
    for raw_ln in log_text.splitlines():
        if not raw_ln.startswith(METRIC_PREFIX):
            continue
        json_part = raw_ln[len(METRIC_PREFIX):].strip()
        try:
            obj = json.loads(json_part)
        except json.JSONDecodeError as exc:
            return {}, {}, f"HARNESS_INVALID: malformed VERIPERF_METRIC JSON ({exc})"

        tier = str(obj.get("tier", "default")).strip() or "default"
        w = int(obj.get("warmup", cli_warmup))
        if w < MIN_WARMUP:
            return {}, {}, f"HARNESS_INVALID: tier={tier} reported warmup={w} < {MIN_WARMUP}"

        if "samples_ns" in obj and isinstance(obj["samples_ns"], list):
            samples_ms = [float(x) / 1_000_000.0 for x in obj["samples_ns"]]
        elif "samples_ms" in obj and isinstance(obj["samples_ms"], list):
            samples_ms = [float(x) for x in obj["samples_ms"]]
        else:
            return {}, {}, f"HARNESS_INVALID: tier={tier} missing samples_ns/samples_ms list"

        if len(samples_ms) < min_runs:
            return {}, {}, f"HARNESS_INVALID: tier={tier} has runs={len(samples_ms)} < {min_runs}"

        if "peak_kb" in obj:
            peak_kb = float(obj["peak_kb"])
        elif "peak_bytes" in obj:
            peak_kb = float(obj["peak_bytes"]) / 1024.0
        else:
            peak_kb = 0.0

        r_hash = str(obj.get("result_hash", "")).strip()
        if not r_hash:
            return {}, {}, f"HARNESS_INVALID: tier={tier} missing result_hash"

        tiers[tier] = _compute_tier_stats(tier, w, samples_ms, peak_kb, r_hash)
        raw_samples[tier] = samples_ms

    return tiers, raw_samples, None


def _run_subprocess_once(
    cmd: List[str],
    cwd: Path,
    env: Dict[str, str],
    timeout: int,
) -> Tuple[int, str, float, float]:
    """Execute command once and return (rc, combined_output, elapsed_ms, peak_rss_kb)."""
    t0 = time.perf_counter_ns()
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        elapsed_ms = (time.perf_counter_ns() - t0) / 1_000_000.0
        return 124, f"{out}\n{err}\n[TIMEOUT after {timeout}s]", elapsed_ms, 0.0

    elapsed_ms = (time.perf_counter_ns() - t0) / 1_000_000.0
    peak_kb = 0.0
    if os.name == "nt":
        handle = getattr(proc, "_handle", None)
        if handle is not None:
            try:
                peak_kb = _get_windows_peak_rss_kb(int(handle))
            except Exception:
                peak_kb = 0.0

    combined = stdout or ""
    if stderr:
        combined = (combined.rstrip("\n") + "\n" + stderr) if combined else stderr
    return int(proc.returncode), strip_ansi(combined), elapsed_ms, peak_kb


def _format_bench_digest(
    target_id: str,
    phase: str,
    warmup: int,
    runs: int,
    tiers: Dict[str, Dict[str, Any]],
    primary_tier: str,
    log_rel: str,
    mode_label: str,
) -> str:
    """Format strictly <=10 lines summary for bench-run."""
    lines = [
        f"[vp bench-run] id={target_id} phase={phase} mode={mode_label} status=OK (warmup={warmup}, runs={runs})",
        "tier     | p50 (ms)  | p95 (ms)  | mean (ms) | cv%   | peak_mem   | hash",
    ]
    order = [t for t in ("small", "medium", "large", "default") if t in tiers]
    order += [t for t in sorted(tiers) if t not in order]
    # Reserve 1 line for footer => max 7 tier rows (2 + 7 + 1 = 10 lines max)
    for t in order[:7]:
        s = tiers[t]
        lines.append(
            f"{t:<8} | {s['p50_ms']:>9.3f} | {s['p95_ms']:>9.3f} | {s['mean_ms']:>9.3f} | "
            f"{s['cv_pct']:>4.1f}% | {s['peak_kb']:>7.1f} KB | {trunc(s['result_hash'], 12)}"
        )
    p = tiers[primary_tier]
    lines.append(
        f"primary={primary_tier} p50={p['p50_ms']:.3f}ms p95={p['p95_ms']:.3f}ms "
        f"peak={p['peak_kb']:.1f}KB json={log_rel}"
    )
    return "\n".join(lines[:10])


def _run_bench_leg(
    root: Path,
    s_dir: Path,
    cache_dir: Path,
    phase: str,
    cmd_tokens: List[str],
    warmup: int,
    runs: int,
    tier_filter: str = "all",
    cwd_arg: Optional[str] = None,
    timeout: int = 300,
    min_runs: int = MIN_RUNS,
) -> Tuple[int, Dict[str, Dict[str, Any]], Dict[str, List[float]], str, Path, str]:
    """Execute one benchmark leg (Mode A VERIPERF_METRIC or Mode B subprocess fallback)."""
    sb_opt = s_dir / "sandbox-opt"
    if cwd_arg:
        cwd = Path(cwd_arg)
        if not cwd.is_absolute():
            cwd = (root / cwd).resolve()
    elif phase == "opt" and sb_opt.is_dir():
        cwd = sb_opt
    else:
        cwd = root

    target_root = sb_opt if (phase == "opt" and sb_opt.is_dir()) else root
    env = build_isolated_env(
        cache_dir,
        target_root=target_root,
        extra_env={
            "VERIPERF_WARMUP": str(warmup),
            "VERIPERF_RUNS": str(runs),
            "VERIPERF_TIER": tier_filter,
        },
    )
    resolved_cmd = resolve_executable(_resolve_bughunt_cmd_paths(cmd_tokens, root), cwd, env)

    rc, first_out, _, first_peak_kb = _run_subprocess_once(resolved_cmd, cwd, env, timeout)
    if rc != 0:
        tail = " | ".join(ln.strip() for ln in first_out.splitlines()[-4:] if ln.strip())
        err_log = f"$ {' '.join(cmd_tokens)}\nCWD: {cwd}\nexit={rc}\n{first_out}\n"
        return 1, {}, {}, f"status=ERROR exit={rc}: {trunc(tail, 180)}", cwd, err_log

    tiers, raw_samples, parse_err = _parse_metric_lines(first_out, warmup, min_runs=min_runs)
    if parse_err:
        return 1, {}, {}, parse_err, cwd, f"$ {' '.join(cmd_tokens)}\nexit={rc}\n{first_out}\n"

    if tiers:
        if tier_filter != "all":
            if tier_filter not in tiers:
                return (
                    1,
                    {},
                    {},
                    f"ERROR: requested --tier {tier_filter} not found in {list(tiers.keys())}",
                    cwd,
                    first_out,
                )
            tiers = {tier_filter: tiers[tier_filter]}
            raw_samples = {tier_filter: raw_samples[tier_filter]}
        raw_log_text = f"$ {' '.join(cmd_tokens)}\nCWD: {cwd}\nexit={rc}\n{first_out}\n"
        return 0, tiers, raw_samples, "harness", cwd, raw_log_text

    # Mode B: external subprocess timing (first execution served as warmup #1)
    for w_idx in range(1, warmup):
        w_rc, w_out, _, _ = _run_subprocess_once(resolved_cmd, cwd, env, timeout)
        if w_rc != 0:
            return 1, {}, {}, f"Warmup {w_idx + 1} failed exit={w_rc}", cwd, w_out

    samples_ms: List[float] = []
    peak_vals: List[float] = []
    hashes: List[str] = []
    last_out = first_out
    for r_idx in range(runs):
        r_rc, r_out, r_ms, r_peak = _run_subprocess_once(resolved_cmd, cwd, env, timeout)
        if r_rc != 0:
            return 1, {}, {}, f"Timed run {r_idx + 1} failed exit={r_rc}", cwd, r_out
        samples_ms.append(r_ms)
        if r_peak > 0:
            peak_vals.append(r_peak)
        norm = r_out.replace("\r\n", "\n").strip()
        hashes.append(hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16])
        last_out = r_out

    unique_hashes = list(dict.fromkeys(hashes))
    final_hash = unique_hashes[0] if len(unique_hashes) == 1 else f"VARIES:{unique_hashes[0]}"
    peak_kb = max(peak_vals) if peak_vals else first_peak_kb
    t_name = "default" if tier_filter == "all" else tier_filter
    tiers = {t_name: _compute_tier_stats(t_name, warmup, samples_ms, peak_kb, final_hash)}
    raw_samples = {t_name: samples_ms}
    raw_log_text = (
        f"$ {' '.join(cmd_tokens)}\nCWD: {cwd}\nmode=subprocess warmup={warmup} runs={runs}\n"
        f"samples_ms={[round(x, 3) for x in samples_ms]}\nlast_stdout:\n{last_out}\n"
    )
    return 0, tiers, raw_samples, "subprocess", cwd, raw_log_text


def execute_bench_run(
    root: Path,
    s_dir: Path,
    cache_dir: Path,
    target_id: str,
    phase: str,
    cmd_tokens: List[str],
    warmup: int,
    runs: int,
    tier_filter: str = "all",
    cwd_arg: Optional[str] = None,
    timeout: int = 300,
) -> Tuple[int, Dict[str, Any], str]:
    """Core statistical benchmark runner used by both bench-run and verify-opt."""
    if warmup < MIN_WARMUP:
        raise SystemExit(f"[vp.py] ERROR: --warmup must be >= {MIN_WARMUP} (got {warmup})")
    if runs < MIN_RUNS:
        raise SystemExit(f"[vp.py] ERROR: --runs must be >= {MIN_RUNS} (got {runs})")
    if not cmd_tokens:
        raise SystemExit("[vp.py] ERROR: Missing command after --")

    log_file = s_dir / "logs" / f"{target_id}_{phase}.log"
    json_file = s_dir / "logs" / f"{target_id}_{phase}.json"

    rc, tiers, _, mode_or_err, cwd, raw_log_text = _run_bench_leg(
        root=root,
        s_dir=s_dir,
        cache_dir=cache_dir,
        phase=phase,
        cmd_tokens=cmd_tokens,
        warmup=warmup,
        runs=runs,
        tier_filter=tier_filter,
        cwd_arg=cwd_arg,
        timeout=timeout,
        min_runs=MIN_RUNS,
    )
    if rc != 0:
        log_file.write_text(raw_log_text, encoding="utf-8")
        return 1, {"status": "ERROR", "error": mode_or_err}, f"[vp bench-run] id={target_id} phase={phase} {mode_or_err}"

    mode_label = mode_or_err
    primary = _select_primary_tier(tiers)
    payload = {
        "id": target_id,
        "phase": phase,
        "mode": mode_label,
        "status": "OK",
        "cmd": list(cmd_tokens),
        "cwd": str(cwd),
        "warmup": warmup,
        "runs": runs,
        "primary_tier": primary,
        "tiers": tiers,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    log_file.write_text(raw_log_text, encoding="utf-8")
    json_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    digest = _format_bench_digest(
        target_id, phase, warmup, runs, tiers, primary, rel_posix(json_file, root), mode_label
    )
    return 0, payload, digest


def cmd_bench_run(args: argparse.Namespace) -> int:
    root, s_dir, _, cache_dir = resolve_session(args.s, args.root)
    tid = sanitize_id(args.id)
    cmd_tokens = parse_cmd_tokens(args.cmd)
    rc, _, digest = execute_bench_run(
        root=root,
        s_dir=s_dir,
        cache_dir=cache_dir,
        target_id=tid,
        phase=args.phase,
        cmd_tokens=cmd_tokens,
        warmup=int(args.warmup),
        runs=int(args.runs),
        tier_filter=args.tier,
        cwd_arg=args.cwd,
        timeout=int(args.timeout),
    )
    print(digest)
    return rc


# ---------------------------------------------------------------------------
# Subcommand: verify-opt (Dual Gate Check)
# ---------------------------------------------------------------------------


_BROKEN_TEST_SIGNALS = (
    (r"\bno tests ran\b", "no tests ran"),
    (r"\bRan 0 tests\b", "Ran 0 tests"),
    (r"collected 0 items", "collected 0 items"),
    (r"No tests found", "No tests found"),
    (r"No tests were executed", "No tests were executed"),
    (r"no test files", "no test files"),
    (r"running 0 tests", "running 0 tests"),
    (r"ERROR collecting\b", "collection error"),
    (r"\bModuleNotFoundError:", "ModuleNotFoundError"),
    (r"\bSyntaxError:", "SyntaxError"),
    (r"COMPILATION ERROR|Compilation failed|could not compile|\[build failed\]|error CS\d+", "compile/build failure"),
)


def _run_correctness_tests(
    root: Path,
    s_dir: Path,
    cache_dir: Path,
    target_id: str,
    test_cmd_str: str,
    cwd_arg: Optional[str],
    timeout: int,
) -> Tuple[bool, str, Path]:
    cmd_tokens = parse_cmd_tokens(test_cmd_str)
    sb_opt = s_dir / "sandbox-opt"
    if cwd_arg:
        cwd = Path(cwd_arg)
        if not cwd.is_absolute():
            cwd = (root / cwd).resolve()
    elif sb_opt.is_dir():
        cwd = sb_opt
    else:
        cwd = root

    target_root = sb_opt if sb_opt.is_dir() else root
    env = build_isolated_env(cache_dir, target_root=target_root)
    resolved = resolve_executable(_resolve_bughunt_cmd_paths(cmd_tokens, root), cwd, env)
    rc, out, elapsed_ms, _ = _run_subprocess_once(resolved, cwd, env, timeout)

    test_log = s_dir / "logs" / f"{target_id}_test.log"
    test_log.write_text(
        f"$ {' '.join(cmd_tokens)}\nCWD: {cwd}\nexit={rc} elapsed={elapsed_ms:.1f}ms\n{out}\n",
        encoding="utf-8",
    )

    broken = [label for pat, label in _BROKEN_TEST_SIGNALS if re.search(pat, out, re.I)]
    if rc != 0:
        return False, f"test_exit={rc}" + (f" ({','.join(broken)})" if broken else ""), test_log
    if broken:
        return False, f"broken_harness ({','.join(broken)})", test_log
    return True, f"PASS ({elapsed_ms:.0f}ms)", test_log


def _compute_tier_comparison(b: Dict[str, Any], o: Dict[str, Any]) -> Dict[str, Any]:
    b_hash = str(b.get("result_hash", ""))
    o_hash = str(o.get("result_hash", ""))
    h_match = bool(
        b_hash and o_hash and b_hash == o_hash
        and not b_hash.startswith("VARIES:") and not o_hash.startswith("VARIES:")
    )
    b_p50, o_p50 = float(b["p50_ms"]), float(o["p50_ms"])
    b_p95, o_p95 = float(b["p95_ms"]), float(o["p95_ms"])
    b_mem, o_mem = float(b.get("peak_kb", 0.0)), float(o.get("peak_kb", 0.0))

    p50_sp = ((b_p50 - o_p50) / b_p50 * 100.0) if b_p50 > 0 else 0.0
    p95_sp = ((b_p95 - o_p95) / b_p95 * 100.0) if b_p95 > 0 else 0.0
    factor = (b_p50 / o_p50) if o_p50 > 0 else 9999.0
    mem_red = ((b_mem - o_mem) / b_mem * 100.0) if b_mem > 0 else 0.0
    mem_growth = ((o_mem - b_mem) / b_mem * 100.0) if b_mem > 0 else 0.0
    return {
        "base_p50_ms": b_p50,
        "opt_p50_ms": o_p50,
        "p50_speedup_pct": round(p50_sp, 2),
        "speedup_factor": round(factor, 2),
        "base_p95_ms": b_p95,
        "opt_p95_ms": o_p95,
        "p95_speedup_pct": round(p95_sp, 2),
        "base_peak_kb": b_mem,
        "opt_peak_kb": o_mem,
        "mem_reduction_pct": round(mem_red, 2),
        "mem_growth_pct": round(mem_growth, 2),
        "base_hash": b_hash,
        "opt_hash": o_hash,
        "hash_match": h_match,
    }


def _should_interleave(
    mode: str,
    primary_comp: Dict[str, Any],
    base_tier: Dict[str, Any],
    opt_tier: Dict[str, Any],
    min_speedup: float,
) -> Tuple[bool, str]:
    if mode == "off":
        return False, ""
    if mode == "always":
        return True, "forced (--interleave=always)"
    p50_gain = float(primary_comp["p50_speedup_pct"])
    if abs(p50_gain - min_speedup) <= 3.0:
        return True, f"borderline speedup ({p50_gain:+.1f}% near {min_speedup:.1f}%)"
    max_cv = max(float(base_tier.get("cv_pct", 0.0)), float(opt_tier.get("cv_pct", 0.0)))
    if max_cv > 5.0:
        return True, f"high variance (cv={max_cv:.1f}% > 5.0%)"
    return False, ""


def _run_interleaved_ab(
    root: Path,
    s_dir: Path,
    cache_dir: Path,
    base_cmd: List[str],
    opt_cmd: List[str],
    primary_tier: str,
    rounds: int,
    runs_per_phase: int,
    base_cwd: Optional[str],
    opt_cwd: Optional[str],
    timeout: int,
) -> Tuple[bool, Dict[str, Any], Dict[str, Any], str]:
    """Run counterbalanced ABBA interleaved rounds on primary_tier to defeat thermal/CPU drift."""
    leg_warmup = MIN_WARMUP
    leg_runs = max(8, runs_per_phase // 2)
    pooled_samples: Dict[str, List[float]] = {"baseline": [], "opt": []}
    pooled_peaks: Dict[str, List[float]] = {"baseline": [], "opt": []}
    pooled_hashes: Dict[str, List[str]] = {"baseline": [], "opt": []}

    for r_idx in range(max(2, rounds)):
        pair = ("baseline", "opt") if r_idx % 2 == 0 else ("opt", "baseline")
        for leg_phase in pair:
            cmd_tok = base_cmd if leg_phase == "baseline" else opt_cmd
            cwd_val = base_cwd if leg_phase == "baseline" else opt_cwd
            rc, tiers, raw_s, mode_err, _, _ = _run_bench_leg(
                root=root,
                s_dir=s_dir,
                cache_dir=cache_dir,
                phase=leg_phase,
                cmd_tokens=cmd_tok,
                warmup=leg_warmup,
                runs=leg_runs,
                tier_filter=primary_tier,
                cwd_arg=cwd_val,
                timeout=timeout,
                min_runs=leg_runs,
            )
            if rc != 0 or primary_tier not in tiers:
                return False, {}, {}, f"interleave leg failed ({leg_phase}): {mode_err}"
            pooled_samples[leg_phase].extend(raw_s[primary_tier])
            pooled_peaks[leg_phase].append(float(tiers[primary_tier].get("peak_kb", 0.0)))
            pooled_hashes[leg_phase].append(str(tiers[primary_tier].get("result_hash", "")))

    def _agg_hash(hs: List[str]) -> str:
        uniq = list(dict.fromkeys(hs))
        return uniq[0] if len(uniq) == 1 else f"VARIES:{uniq[0]}"

    new_base = _compute_tier_stats(
        primary_tier,
        leg_warmup,
        pooled_samples["baseline"],
        statistics.median(pooled_peaks["baseline"]),
        _agg_hash(pooled_hashes["baseline"]),
    )
    new_opt = _compute_tier_stats(
        primary_tier,
        leg_warmup,
        pooled_samples["opt"],
        statistics.median(pooled_peaks["opt"]),
        _agg_hash(pooled_hashes["opt"]),
    )
    return True, new_base, new_opt, f"{rounds} counterbalanced rounds x {leg_runs} samples"


def _checkpoint_dir(s_dir: Path, name: str) -> Path:
    return s_dir / "tmp" / "checkpoints" / sanitize_id(name)


def _save_sandbox_checkpoint(root: Path, s_dir: Path, name: str) -> Tuple[Path, int]:
    opt_dir = s_dir / "sandbox-opt"
    if not opt_dir.is_dir():
        raise SystemExit(f"[vp.py] ERROR: {opt_dir} not found; run sandbox --mode opt first")
    dest = _checkpoint_dir(s_dir, name)
    if dest.exists():
        _remove_readonly_and_rmtree(dest)
    global_excl, root_excl = _compute_sandbox_exclusions(root, [])
    copied = _copy_project_tree(opt_dir, dest, global_excl, root_excl)
    return dest, copied


def _restore_sandbox_from(root: Path, s_dir: Path, src_dir: Path) -> int:
    opt_dir = s_dir / "sandbox-opt"
    if opt_dir.exists():
        _remove_readonly_and_rmtree(opt_dir)
    global_excl, root_excl = _compute_sandbox_exclusions(root, [])
    return _copy_project_tree(src_dir, opt_dir, global_excl, root_excl)


def cmd_verify_opt(args: argparse.Namespace) -> int:
    root, s_dir, _, cache_dir = resolve_session(args.s, args.root)
    tid = sanitize_id(args.id)

    min_speedup = float(args.min_speedup)
    if min_speedup < HARD_MIN_SPEEDUP_PCT:
        raise SystemExit(
            f"[vp.py] ERROR: --min-speedup={min_speedup} violates hard floor ({HARD_MIN_SPEEDUP_PCT:.1f}%)"
        )
    max_mem_growth = float(args.max_mem_growth)
    if max_mem_growth < 0.0:
        raise SystemExit(f"[vp.py] ERROR: --max-mem-growth must be >= 0.0 (got {max_mem_growth})")

    base_json_path = s_dir / "logs" / f"{tid}_baseline.json"
    if not base_json_path.is_file():
        raise SystemExit(f"[vp.py] ERROR: Baseline metrics missing: {base_json_path}")
    base_data = json.loads(base_json_path.read_text(encoding="utf-8"))

    opt_json_path = s_dir / "logs" / f"{tid}_opt.json"
    if args.opt_bench_cmd or args.rerun or not opt_json_path.is_file():
        opt_tokens = parse_cmd_tokens(args.opt_bench_cmd) if args.opt_bench_cmd else list(base_data.get("cmd", []))
        if not opt_tokens:
            raise SystemExit("[vp.py] ERROR: No opt benchmark command provided or found in baseline JSON")
        rc, opt_data, opt_err = execute_bench_run(
            root=root,
            s_dir=s_dir,
            cache_dir=cache_dir,
            target_id=tid,
            phase="opt",
            cmd_tokens=opt_tokens,
            warmup=int(args.warmup),
            runs=int(args.runs),
            tier_filter="all",
            cwd_arg=args.cwd,
            timeout=int(args.timeout),
        )
        if rc != 0:
            print(opt_err)
            return rc
    else:
        opt_data = json.loads(opt_json_path.read_text(encoding="utf-8"))

    # Gate 1: Correctness tests
    test_ok = True
    test_detail = "SKIPPED (no --test-cmd)"
    if args.test_cmd:
        test_ok, test_detail, _ = _run_correctness_tests(
            root, s_dir, cache_dir, tid, args.test_cmd, args.cwd, int(args.timeout)
        )

    base_tiers: Dict[str, Dict[str, Any]] = base_data.get("tiers", {})
    opt_tiers: Dict[str, Dict[str, Any]] = opt_data.get("tiers", {})
    shared_tiers = [t for t in ("small", "medium", "large", "default") if t in base_tiers and t in opt_tiers]
    shared_tiers += [t for t in sorted(base_tiers) if t in opt_tiers and t not in shared_tiers]

    if not shared_tiers:
        raise SystemExit("[vp.py] ERROR: Baseline and opt logs share no matching workload tiers")

    comparisons: Dict[str, Dict[str, Any]] = {
        t: _compute_tier_comparison(base_tiers[t], opt_tiers[t]) for t in shared_tiers
    }
    primary_tier = _select_primary_tier({t: base_tiers[t] for t in shared_tiers}, preferred=args.primary_tier)

    # Interleaved A/B Re-Verification on borderline or high-variance results
    interleave_info: Dict[str, Any] = {"triggered": False, "reason": ""}
    should_il, il_reason = _should_interleave(
        args.interleave, comparisons[primary_tier], base_tiers[primary_tier], opt_tiers[primary_tier], min_speedup
    )
    if should_il and test_ok:
        base_cmd = list(base_data.get("cmd", []))
        opt_cmd = list(opt_data.get("cmd", base_cmd))
        if base_cmd and opt_cmd:
            il_ok, il_base, il_opt, il_detail = _run_interleaved_ab(
                root=root,
                s_dir=s_dir,
                cache_dir=cache_dir,
                base_cmd=base_cmd,
                opt_cmd=opt_cmd,
                primary_tier=primary_tier,
                rounds=int(args.interleave_rounds),
                runs_per_phase=int(args.runs),
                base_cwd=base_data.get("cwd"),
                opt_cwd=args.cwd,
                timeout=int(args.timeout),
            )
            if il_ok:
                base_tiers[primary_tier] = il_base
                opt_tiers[primary_tier] = il_opt
                comparisons[primary_tier] = _compute_tier_comparison(il_base, il_opt)
                interleave_info = {"triggered": True, "reason": il_reason, "detail": il_detail}
            else:
                interleave_info = {"triggered": False, "reason": f"{il_reason} (skipped: {il_detail})"}

    hash_mismatches = [
        f"{t}({comparisons[t]['base_hash']}!={comparisons[t]['opt_hash']})"
        for t in shared_tiers
        if not comparisons[t]["hash_match"]
    ]
    gate1_pass = bool(test_ok and not hash_mismatches)
    if not test_ok:
        gate1_status = f"FAIL (TEST_FAILURE: {test_detail})"
    elif hash_mismatches:
        gate1_status = f"FAIL (OUTPUT_HASH_MISMATCH: {','.join(hash_mismatches)})"
    else:
        gate1_status = f"PASS (tests={test_detail}, hashes=MATCH)"

    pc = comparisons[primary_tier]
    p50_gain = pc["p50_speedup_pct"]
    p95_gain = pc["p95_speedup_pct"]
    mem_growth = pc["mem_growth_pct"]
    abs_mem_delta_kb = pc["opt_peak_kb"] - pc["base_peak_kb"]
    mem_blowup = (
        (not args.allow_mem_tradeoff)
        and abs_mem_delta_kb > MEM_NOISE_DEADBAND_KB
        and mem_growth > max_mem_growth
    )

    if p50_gain < min_speedup:
        gate2_pass = False
        gate2_status = f"FAIL (INSUFFICIENT_GAIN: {p50_gain:+.1f}% < {min_speedup:.1f}%)"
    elif p95_gain < 0.0:
        gate2_pass = False
        gate2_status = f"FAIL (P95_REGRESSION: {p95_gain:+.1f}% < 0.0%)"
    elif mem_blowup:
        gate2_pass = False
        gate2_status = f"FAIL (MEMORY_BLOWUP: +{mem_growth:.1f}% > +{max_mem_growth:.1f}% ceiling)"
    else:
        gate2_pass = True
        mem_note = "mem=TRADEOFF_ALLOWED" if args.allow_mem_tradeoff else f"mem={mem_growth:+.1f}%<=+{max_mem_growth:.0f}%"
        gate2_status = f"PASS ({p50_gain:+.1f}% >= {min_speedup:.1f}%, p95={p95_gain:+.1f}%, {mem_note})"

    if gate1_pass and gate2_pass:
        verdict = "ACCEPTED"
        reason = "DUAL_GATE_PASSED"
    elif not test_ok:
        verdict = "REJECTED"
        reason = "REJECTED_TEST_FAILURE"
    elif hash_mismatches:
        verdict = "REJECTED"
        reason = "REJECTED_OUTPUT_MISMATCH"
    elif p50_gain < min_speedup:
        verdict = "REJECTED"
        reason = "REJECTED_INSUFFICIENT_GAIN"
    elif p95_gain < 0.0:
        verdict = "REJECTED"
        reason = "REJECTED_P95_REGRESSION"
    else:
        verdict = "REJECTED"
        reason = "REJECTED_MEMORY_BLOWUP"

    # Deterministic Sandbox Checkpoint / Auto-Rollback
    rollback_info: Dict[str, Any] = {"restored": False, "source": ""}
    if (s_dir / "sandbox-opt").is_dir():
        if verdict == "ACCEPTED":
            _save_sandbox_checkpoint(root, s_dir, "_last_accepted")
        elif args.auto_rollback:
            cand_id = _checkpoint_dir(s_dir, tid)
            cand_last = _checkpoint_dir(s_dir, "_last_accepted")
            if cand_id.is_dir():
                src_dir, src_lbl = cand_id, f"checkpoint:{tid}"
            elif cand_last.is_dir():
                src_dir, src_lbl = cand_last, "checkpoint:_last_accepted"
            else:
                src_dir, src_lbl = root, "ROOT"
            _restore_sandbox_from(root, s_dir, src_dir)
            rollback_info = {"restored": True, "source": src_lbl}

    verify_payload = {
        "id": tid,
        "verdict": verdict,
        "reason": reason,
        "min_speedup_pct": min_speedup,
        "max_mem_growth_pct": max_mem_growth,
        "allow_mem_tradeoff": bool(args.allow_mem_tradeoff),
        "primary_tier": primary_tier,
        "gate1_pass": gate1_pass,
        "gate1_detail": gate1_status,
        "gate2_pass": gate2_pass,
        "gate2_detail": gate2_status,
        "test_status": test_detail,
        "interleaved_ab": interleave_info,
        "rollback": rollback_info,
        "comparisons": comparisons,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    verify_path = s_dir / "logs" / f"{tid}_verify.json"
    verify_path.write_text(json.dumps(verify_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    # Compact <=12-line digest
    out_lines = [
        f"[vp verify-opt] id={tid} primary={primary_tier} verdict={verdict} ({reason})",
        "tier     | base_p50  | opt_p50   | speedup | base_p95  | opt_p95   | mem_delta | hash",
    ]
    for t in shared_tiers[:5]:
        c = comparisons[t]
        h_lbl = "MATCH" if c["hash_match"] else "MISMATCH"
        out_lines.append(
            f"{t:<8} | {c['base_p50_ms']:>7.2f}ms | {c['opt_p50_ms']:>7.2f}ms | {c['p50_speedup_pct']:>+6.1f}% | "
            f"{c['base_p95_ms']:>7.2f}ms | {c['opt_p95_ms']:>7.2f}ms | {c['mem_reduction_pct']:>+8.1f}% | {h_lbl}"
        )
    out_lines.append(f"Gate 1 (Correctness): {gate1_status}")
    out_lines.append(f"Gate 2 (>={min_speedup:.0f}% Speed / <=+{max_mem_growth:.0f}% Mem): {gate2_status}")
    extras: List[str] = [f"saved={rel_posix(verify_path, root)}"]
    if interleave_info["triggered"]:
        extras.append(f"interleaved=YES ({interleave_info['reason']})")
    if rollback_info["restored"]:
        extras.append(f"rollback=AUTO_RESTORED ({rollback_info['source']})")
    out_lines.append(" | ".join(extras))
    print("\n".join(out_lines[:12]))
    return 0


# ---------------------------------------------------------------------------
# Subcommand: sandbox (create | opt | diff | clean)
# ---------------------------------------------------------------------------


def _compute_sandbox_exclusions(root: Path, extra: Sequence[str]) -> Tuple[set[str], set[str]]:
    global_excl = set(SANDBOX_ALWAYS_EXCLUDE)
    root_excl: set[str] = set()
    if (root / "Cargo.toml").is_file():
        root_excl.add("target")
    if (root / "pom.xml").is_file() or any((root / f).is_file() for f in ("build.gradle", "build.gradle.kts")):
        root_excl.update({"target", "build", "out"})
    if any(root.glob("*.sln")) or any(root.glob("*.csproj")):
        global_excl.update({"bin", "obj"})
    for item in extra:
        c = item.strip().strip("/\\")
        if c and c != "vendor":
            global_excl.add(c)
    global_excl.discard("vendor")
    root_excl.discard("vendor")
    return global_excl, root_excl


def _copy_project_tree(root: Path, dest: Path, global_excl: set[str], root_excl: set[str]) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    root_res = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root_res):
        dp = Path(dirpath)
        rel_dir = dp.relative_to(root_res)
        at_root = rel_dir == Path(".")
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in global_excl and not (at_root and d in root_excl) and not ((dp / d).is_symlink() and not (dp / d).exists())
        )
        out_dir = dest if at_root else (dest / rel_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for fname in sorted(filenames):
            src_f = dp / fname
            dst_f = out_dir / fname
            try:
                if src_f.is_symlink() and not src_f.exists():
                    continue
                shutil.copy2(src_f, dst_f, follow_symlinks=True)
                copied += 1
            except OSError:
                continue
    return copied


def _collect_source_files_for_diff(base: Path, global_excl: set[str], root_excl: set[str]) -> Dict[str, Path]:
    files: Dict[str, Path] = {}
    if not base.is_dir():
        return files
    base_res = base.resolve()
    for dirpath, dirnames, filenames in os.walk(base_res):
        dp = Path(dirpath)
        rel_dir = dp.relative_to(base_res)
        at_root = rel_dir == Path(".")
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in global_excl and not (at_root and d in root_excl)
        )
        for fname in sorted(filenames):
            fp = dp / fname
            if fp.suffix.lower() in BINARY_EXTS or fname.lower().endswith((".pyc", ".pyo", ".class")):
                continue
            rel = fname if at_root else f"{rel_dir.as_posix()}/{fname}"
            files[rel] = fp
    return files


def _read_text_lines_for_diff(fp: Path) -> Optional[List[str]]:
    try:
        if fp.stat().st_size > 2_000_000:
            return None
        raw = fp.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:4096]:
        return None
    return raw.decode("utf-8", errors="replace").splitlines()


def cmd_sandbox(args: argparse.Namespace) -> int:
    root, s_dir, _, cache_dir = resolve_session(args.s, args.root)
    global_excl, root_excl = _compute_sandbox_exclusions(root, args.exclude or [])

    if args.mode in ("create", "opt"):
        dir_name = "sandbox" if args.mode == "create" else "sandbox-opt"
        target = s_dir / dir_name
        if target.exists():
            if not args.force:
                raise SystemExit(f"[vp.py] ERROR: {target} already exists (pass --force to overwrite)")
            _remove_readonly_and_rmtree(target)
        t0 = time.monotonic()
        copied = _copy_project_tree(root, target, global_excl, root_excl)
        dur = time.monotonic() - t0
        print(f"[vp sandbox {args.mode}] path={rel_posix(target, root)} files={copied} duration={dur:.2f}s")
        return 0

    if args.mode == "save":
        if not args.id:
            raise SystemExit("[vp.py] ERROR: --id is required for sandbox --mode save")
        dest, copied = _save_sandbox_checkpoint(root, s_dir, args.id)
        print(f"[vp sandbox save] id={sanitize_id(args.id)} path={rel_posix(dest, root)} files={copied}")
        return 0

    if args.mode == "rollback":
        if not args.id:
            raise SystemExit("[vp.py] ERROR: --id is required for sandbox --mode rollback")
        ckpt = _checkpoint_dir(s_dir, args.id)
        if ckpt.is_dir():
            src_dir, src_lbl = ckpt, rel_posix(ckpt, root)
        else:
            src_dir, src_lbl = root, "ROOT"
        copied = _restore_sandbox_from(root, s_dir, src_dir)
        print(f"[vp sandbox rollback] id={sanitize_id(args.id)} restored_from={src_lbl} files={copied}")
        return 0

    if args.mode == "diff":
        opt_dir = s_dir / "sandbox-opt"
        if not opt_dir.is_dir():
            raise SystemExit(f"[vp.py] ERROR: {opt_dir} not found; run --mode opt first")
        sb_dir = s_dir / "sandbox"
        base_dir = sb_dir if sb_dir.is_dir() else root
        base_map = _collect_source_files_for_diff(base_dir, global_excl, root_excl)
        opt_map = _collect_source_files_for_diff(opt_dir, global_excl, root_excl)

        file_diffs: List[Tuple[str, str]] = []
        for rel in sorted(set(base_map) | set(opt_map)):
            b_fp, o_fp = base_map.get(rel), opt_map.get(rel)
            a_lines = _read_text_lines_for_diff(b_fp) if b_fp is not None else []
            b_lines = _read_text_lines_for_diff(o_fp) if o_fp is not None else []
            if a_lines is None or b_lines is None or a_lines == b_lines:
                continue
            udiff = list(difflib.unified_diff(a_lines, b_lines, fromfile=f"a/{rel}", tofile=f"b/{rel}", n=2, lineterm=""))
            if udiff:
                file_diffs.append((rel, "\n".join(udiff)))

        full_diff_text = "\n\n".join(d for _, d in file_diffs)
        diff_file = s_dir / "logs" / "sandbox-opt.diff"
        diff_file.write_text(full_diff_text + ("\n" if full_diff_text else ""), encoding="utf-8")

        header = f"[vp sandbox diff] files_changed={len(file_diffs)} saved={rel_posix(diff_file, root)}"
        if not file_diffs:
            print(f"{header} (no changes)")
            return 0

        max_out = 7900
        out = header + "\n" + full_diff_text
        if len(out) > max_out:
            note = f"\n... (truncated to <=8000 chars; full diff in {rel_posix(diff_file, root)})"
            out = out[: max_out - len(note)] + note
        print(out)
        return 0

    # mode == "clean"
    cleaned: List[str] = []
    for label, target in (
        ("sandbox", s_dir / "sandbox"),
        ("sandbox-opt", s_dir / "sandbox-opt"),
        ("tmp", s_dir / "tmp"),
        (".cache", cache_dir),
    ):
        if target.exists():
            _remove_readonly_and_rmtree(target)
            cleaned.append(label)

    before_file = s_dir / "logs" / "fs-before.txt"
    mode_file = s_dir / "logs" / ".snapshot-mode"
    snap_mode = mode_file.read_text(encoding="utf-8").strip() if mode_file.is_file() else None
    before_lines = before_file.read_text(encoding="utf-8", errors="replace").splitlines() if before_file.is_file() else []

    actual_mode, after_lines = take_snapshot(root, forced_mode=snap_mode)
    diff = diff_snapshots(before_lines, after_lines, actual_mode)
    removed_caches = _clean_new_agent_caches(root, diff, actual_mode)
    if removed_caches:
        actual_mode, after_lines = take_snapshot(root, forced_mode=actual_mode)
        diff = diff_snapshots(before_lines, after_lines, actual_mode)

    after_file = s_dir / "logs" / "fs-after.txt"
    after_file.write_text(("\n".join(after_lines) + "\n") if after_lines else "", encoding="utf-8")

    dirty_items = [
        f"{k}: {it}"
        for k, items in diff.items()
        if k not in ("resolved", "removed")
        for it in items
    ]
    lines = [
        f"[vp sandbox clean] removed_dirs=[{', '.join(cleaned) or 'none'}] "
        f"auto_removed_caches=[{', '.join(removed_caches) or 'none'}]",
    ]
    if not dirty_items:
        lines.append("cleanliness: CLEAN (0 new/modified files outside .bughunt/)")
    else:
        lines.append(f"cleanliness: DIRTY ({len(dirty_items)} changes outside .bughunt/):")
        for item in dirty_items[:8]:
            lines.append(f"  - {trunc(item, 140)}")
    print("\n".join(lines))
    return 0


# ---------------------------------------------------------------------------
# Subcommand: summary
# ---------------------------------------------------------------------------


def cmd_summary(args: argparse.Namespace) -> int:
    root, s_dir, _, _ = resolve_session(args.s, args.root)
    logs_dir = s_dir / "logs"
    if not logs_dir.is_dir():
        raise SystemExit(f"[vp.py] ERROR: logs directory missing: {logs_dir}")

    scan_file = logs_dir / "hotspot-scan.json"
    scan_count = 0
    if scan_file.is_file():
        scan_count = int(json.loads(scan_file.read_text(encoding="utf-8")).get("hotspot_count", 0))

    verify_files = sorted(logs_dir.glob("*_verify.json"))
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for vf in verify_files:
        data = json.loads(vf.read_text(encoding="utf-8"))
        if data.get("verdict") == "ACCEPTED":
            accepted.append(data)
        else:
            rejected.append(data)

    out: List[str] = [
        f"### VeriPerf Session Summary (`{s_dir.name}`)",
        f"- **Hotspots Scanned:** {scan_count} | **Verified Targets:** {len(verify_files)} "
        f"| **ACCEPTED (>=15%):** {len(accepted)} | **REJECTED:** {len(rejected)}",
        "",
    ]

    if accepted:
        out.append("#### ACCEPTED Optimizations")
        out.append("| ID | Tier | Base p50 | Opt p50 | Speedup | Base p95 | Opt p95 | Base Mem | Opt Mem | Mem Delta | Hash | Verdict |")
        out.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|")
        for d in accepted:
            pt = d["primary_tier"]
            for t, c in d["comparisons"].items():
                tag = f"{t}*" if t == pt else t
                out.append(
                    f"| {d['id']} | {tag} | {c['base_p50_ms']:.3f} ms | {c['opt_p50_ms']:.3f} ms | "
                    f"**+{c['p50_speedup_pct']:.1f}% ({c['speedup_factor']:.2f}x)** | "
                    f"{c['base_p95_ms']:.3f} ms | {c['opt_p95_ms']:.3f} ms | "
                    f"{c['base_peak_kb']:.1f} KB | {c['opt_peak_kb']:.1f} KB | {c['mem_reduction_pct']:+.1f}% | "
                    f"`{c['base_hash'][:8]}` | ACCEPTED |"
                )
        out.append("")

    if rejected:
        out.append("#### REJECTED / Filtered Candidates")
        out.append("| ID | Primary Tier | Base p50 | Opt p50 | Speedup % | Reason | Verdict |")
        out.append("|---|---|---:|---:|---:|---|---|")
        for d in rejected:
            pt = d["primary_tier"]
            c = d["comparisons"][pt]
            out.append(
                f"| {d['id']} | {pt} | {c['base_p50_ms']:.3f} ms | {c['opt_p50_ms']:.3f} ms | "
                f"{c['p50_speedup_pct']:+.1f}% | {d['reason']} | REJECTED |"
            )

    if not accepted and not rejected:
        out.append("No verified optimization targets recorded yet.")

    print("\n".join(out))
    return 0


# ---------------------------------------------------------------------------
# Argument Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vp.py",
        description="VeriPerf v1.1 CLI: hotspot scan, statistical benchmarking, dual-gate verification & checkpoints.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_init = sub.add_parser("init", help="Initialize .bughunt/perf-<ts>/ session and fs-before.txt")
    p_init.add_argument("--root", default=None, help="Project ROOT directory")
    p_init.set_defaults(func=cmd_init)

    p_scan = sub.add_parser("hotspot-scan", help="Scan codebase for nested loops, N+1 I/O, unbounded reads, loop allocs")
    p_scan.add_argument("--root", default=None, help="Project ROOT directory")
    p_scan.add_argument("--scope", default=None, help="Optional relative subpath to scope scan")
    p_scan.add_argument("--s", default=None, help="Optional session directory to store hotspot-scan.json")
    p_scan.add_argument("--max-lines", type=int, default=35, help="Max output lines (<=35)")
    p_scan.add_argument("--json", action="store_true", help="Emit single-line JSON output")
    p_scan.set_defaults(func=cmd_hotspot_scan)

    p_bench = sub.add_parser("bench-run", help="Run warmup (3-5) + timed (15-30) runs and emit <=10-line p50/p95/mem digest")
    p_bench.add_argument("--s", required=True, help="Session directory path")
    p_bench.add_argument("--id", required=True, help="Target optimization ID (e.g. opt1)")
    p_bench.add_argument("--phase", required=True, choices=("baseline", "opt"), help="Benchmark phase")
    p_bench.add_argument("--warmup", type=int, default=5, help="Warm-up iterations (>=3, default 5)")
    p_bench.add_argument("--runs", type=int, default=20, help="Measured iterations (>=15, default 20)")
    p_bench.add_argument("--tier", choices=("all", "small", "medium", "large"), default="all", help="Workload tier")
    p_bench.add_argument("--cwd", default=None, help="Working directory override")
    p_bench.add_argument("--root", default=None, help="Project ROOT override")
    p_bench.add_argument("--timeout", type=int, default=300, help="Timeout seconds per command")
    p_bench.add_argument("cmd", nargs=argparse.REMAINDER, help="Benchmark command after --")
    p_bench.set_defaults(func=cmd_bench_run)

    p_ver = sub.add_parser("verify-opt", help="Run Dual Gate Check (correctness/hash, >=15% speedup, mem ceiling, ABBA interleave)")
    p_ver.add_argument("--s", required=True, help="Session directory path")
    p_ver.add_argument("--id", required=True, help="Target optimization ID")
    p_ver.add_argument("--min-speedup", type=float, default=15.0, help="Minimum p50 speedup %% (>=15.0)")
    p_ver.add_argument("--max-mem-growth", type=float, default=DEFAULT_MAX_MEM_GROWTH_PCT, help="Max peak memory growth %% (default 25.0)")
    p_ver.add_argument("--allow-mem-tradeoff", action="store_true", help="Bypass memory ceiling when user allows RAM-for-speed tradeoff")
    p_ver.add_argument("--interleave", choices=("auto", "always", "off"), default="auto", help="Counterbalanced A/B re-verification mode")
    p_ver.add_argument("--interleave-rounds", type=int, default=4, help="ABBA round pairs when interleaved (default 4)")
    p_ver.add_argument("--auto-rollback", action="store_true", help="Automatically restore S/sandbox-opt to checkpoint on REJECTED")
    p_ver.add_argument("--primary-tier", default="large", help="Primary workload tier (default: large)")
    p_ver.add_argument("--opt-bench-cmd", default=None, help="Benchmark command string for sandbox-opt")
    p_ver.add_argument("--test-cmd", default=None, help="Project test command string for Gate 1")
    p_ver.add_argument("--cwd", default=None, help="Working directory override")
    p_ver.add_argument("--rerun", action="store_true", help="Force re-running opt benchmark even if cached")
    p_ver.add_argument("--warmup", type=int, default=5, help="Warmup count if opt bench runs")
    p_ver.add_argument("--runs", type=int, default=20, help="Timed run count if opt bench runs")
    p_ver.add_argument("--timeout", type=int, default=300, help="Timeout seconds")
    p_ver.add_argument("--root", default=None, help="Project ROOT override")
    p_ver.set_defaults(func=cmd_verify_opt)

    p_sb = sub.add_parser("sandbox", help="Manage S/sandbox-opt, per-target checkpoints, compact diff, and cleanliness check")
    p_sb.add_argument("--s", required=True, help="Session directory path")
    p_sb.add_argument("--mode", required=True, choices=("create", "opt", "save", "rollback", "diff", "clean"), help="Sandbox action")
    p_sb.add_argument("--id", default=None, help="Target ID for save/rollback checkpoint")
    p_sb.add_argument("--exclude", action="append", default=[], help="Extra dir names to exclude")
    p_sb.add_argument("--force", action="store_true", help="Overwrite existing sandbox directory")
    p_sb.add_argument("--root", default=None, help="Project ROOT override")
    p_sb.set_defaults(func=cmd_sandbox)

    p_log = sub.add_parser("log", help="Update worklog.md line-1 Status and append entries")
    p_log.add_argument("--s", required=True, help="Session directory path")
    p_log.add_argument("--status", default=None, help="New Status line text")
    p_log.add_argument("--entry", action="append", default=None, help="Entry text (repeatable)")
    p_log.add_argument("--entry-file", default=None, help="Optional file of entries")
    p_log.add_argument("--root", default=None, help="Project ROOT override")
    p_log.set_defaults(func=cmd_log)

    p_sum = sub.add_parser("summary", help="Emit Markdown summary tables from verified session JSONs")
    p_sum.add_argument("--s", required=True, help="Session directory path")
    p_sum.add_argument("--root", default=None, help="Project ROOT override")
    p_sum.set_defaults(func=cmd_summary)

    p_ch = sub.add_parser("canonical-hash", help="Compute canonical SHA-256[:16] digest for JSON data")
    p_ch.add_argument("--float-digits", type=int, default=6, help="Decimal digits to round floats (default 6)")
    p_ch.add_argument("--sig-figs", type=int, default=None, help="Round floats to N significant figures (scientific mode)")
    p_ch.add_argument("--unordered-lists", action="store_true", help="Sort list elements canonically before hashing")
    p_ch.add_argument("--json-file", default=None, help="Read JSON payload from file (avoids Windows CLI length limits)")
    p_ch.add_argument("--json-str", default=None, help="JSON string (reads stdin if omitted or '-')")
    p_ch.set_defaults(func=cmd_canonical_hash)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
