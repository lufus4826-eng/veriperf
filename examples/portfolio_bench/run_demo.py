#!/usr/bin/env python3
"""One-command live VeriPerf v1.1 demo using an isolated temporary copy of portfolio_bench."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


EXAMPLE_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXAMPLE_DIR.parents[1]
VP_SCRIPT = REPO_ROOT / "scripts" / "vp.py"

BENCH_OPT1 = '''import gc, json, os, sys, time, tracemalloc
from pathlib import Path
DEFAULT_ROOT = Path(__file__).resolve().parents[3]
TARGET_ROOT = Path(os.environ.get("VERIPERF_TARGET_ROOT", str(DEFAULT_ROOT)))
sys.path.insert(0, str(TARGET_ROOT))
sys.path.insert(0, str(Path(r"__VP_DIR__")))
from vp import canonical_hash
from analytics.reconcile import reconcile_trades

WARMUP = int(os.environ.get("VERIPERF_WARMUP", "5"))
RUNS = int(os.environ.get("VERIPERF_RUNS", "20"))
REQ = os.environ.get("VERIPERF_TIER", "all")
for tier, n in [("small", 100), ("medium", 500), ("large", 1500)]:
    if REQ not in ("all", tier): continue
    orders = [{"order_id": f"ORD-{i}", "qty": (i % 5) + 1} for i in range(n)]
    settlements = [{"order_id": f"ORD-{(i * 7) % n}", "qty": 1, "price": 10.25 + (i % 13)} for i in range(n * 2)]
    out = None
    for _ in range(WARMUP): out = reconcile_trades(orders, settlements)
    gc.collect()
    samples = []
    for _ in range(RUNS):
        t0 = time.perf_counter_ns()
        out = reconcile_trades(orders, settlements)
        samples.append(time.perf_counter_ns() - t0)
    gc.collect(); tracemalloc.start(); tracemalloc.reset_peak()
    _ = reconcile_trades(orders, settlements)
    _, peak_b = tracemalloc.get_traced_memory(); tracemalloc.stop()
    print("VERIPERF_METRIC:" + json.dumps({"tier": tier, "warmup": WARMUP, "runs": RUNS, "samples_ns": samples, "peak_kb": round(peak_b / 1024.0, 2), "result_hash": canonical_hash(out)}))
'''

BENCH_OPT2 = '''import gc, json, os, sys, time, tracemalloc
from pathlib import Path
DEFAULT_ROOT = Path(__file__).resolve().parents[3]
TARGET_ROOT = Path(os.environ.get("VERIPERF_TARGET_ROOT", str(DEFAULT_ROOT)))
sys.path.insert(0, str(TARGET_ROOT))
sys.path.insert(0, str(Path(r"__VP_DIR__")))
from vp import canonical_hash
from analytics.audit_agg import aggregate_audit_logs

WARMUP = int(os.environ.get("VERIPERF_WARMUP", "5"))
RUNS = int(os.environ.get("VERIPERF_RUNS", "20"))
REQ = os.environ.get("VERIPERF_TIER", "all")
syms = ["NVDA", "MSFT", "AAPL", "GOOG", "AMZN"]
for tier, n in [("small", 200), ("medium", 2000), ("large", 10000)]:
    if REQ not in ("all", tier): continue
    lines = [json.dumps({"ok": (i % 7) != 0, "code": f"{syms[i % 5]}-{1000 + (i % 9000)}" if i % 11 else "bad-tag", "amount": (i % 500) + 1}) for i in range(n)]
    out = None
    for _ in range(WARMUP): out = aggregate_audit_logs(lines)
    gc.collect()
    samples = []
    for _ in range(RUNS):
        t0 = time.perf_counter_ns()
        out = aggregate_audit_logs(lines)
        samples.append(time.perf_counter_ns() - t0)
    gc.collect(); tracemalloc.start(); tracemalloc.reset_peak()
    _ = aggregate_audit_logs(lines)
    _, peak_b = tracemalloc.get_traced_memory(); tracemalloc.stop()
    print("VERIPERF_METRIC:" + json.dumps({"tier": tier, "warmup": WARMUP, "runs": RUNS, "samples_ns": samples, "peak_kb": round(peak_b / 1024.0, 2), "result_hash": canonical_hash(out)}))
'''

BENCH_OPT3 = '''import gc, json, os, sys, time, tracemalloc
from pathlib import Path
DEFAULT_ROOT = Path(__file__).resolve().parents[3]
TARGET_ROOT = Path(os.environ.get("VERIPERF_TARGET_ROOT", str(DEFAULT_ROOT)))
sys.path.insert(0, str(TARGET_ROOT))
sys.path.insert(0, str(Path(r"__VP_DIR__")))
from vp import canonical_hash
from analytics.fees import compute_batch_fees

WARMUP = int(os.environ.get("VERIPERF_WARMUP", "5"))
RUNS = int(os.environ.get("VERIPERF_RUNS", "20"))
REQ = os.environ.get("VERIPERF_TIER", "all")
for tier, n in [("small", 2000), ("medium", 20000), ("large", 100000)]:
    if REQ not in ("all", tier): continue
    amts = [10.0 + (i % 1000) * 0.37 for i in range(n)]
    out = None
    for _ in range(WARMUP): out = compute_batch_fees(amts)
    gc.collect()
    samples = []
    for _ in range(RUNS):
        t0 = time.perf_counter_ns()
        out = compute_batch_fees(amts)
        samples.append(time.perf_counter_ns() - t0)
    gc.collect(); tracemalloc.start(); tracemalloc.reset_peak()
    _ = compute_batch_fees(amts)
    _, peak_b = tracemalloc.get_traced_memory(); tracemalloc.stop()
    print("VERIPERF_METRIC:" + json.dumps({"tier": tier, "warmup": WARMUP, "runs": RUNS, "samples_ns": samples, "peak_kb": round(peak_b / 1024.0, 2), "result_hash": canonical_hash(out)}))
'''

OPT1_CODE = '''"""Trade reconciliation module (optimized O(N + M) in-place dict index)."""
from __future__ import annotations
from typing import Dict, List

def reconcile_trades(orders: List[Dict], settlements: List[Dict]) -> List[Dict]:
    results: List[Dict] = []
    by_order: Dict[str, Dict] = {}
    for order in orders:
        entry = {"order_id": order["order_id"], "status": "MISMATCH", "filled_qty": 0, "notional": 0.0, "_req": order["qty"]}
        results.append(entry)
        by_order[order["order_id"]] = entry
    for s in settlements:
        entry = by_order.get(s["order_id"])
        if entry is not None:
            q = s["qty"]
            entry["filled_qty"] += q
            entry["notional"] += q * s["price"]
    for entry in results:
        req = entry.pop("_req")
        entry["status"] = "MATCHED" if entry["filled_qty"] == req else "MISMATCH"
        entry["notional"] = round(entry["notional"], 2)
    return results
'''

OPT2_ATTEMPT1 = '''"""Audit log aggregation (Attempt 1: naive list buffer — triggers Gate 2 rejection)."""
from __future__ import annotations
import json, re
from typing import Dict, List

_CODE_RE = re.compile(r"^([A-Z]{2,6})-(\\d{4})$")

def aggregate_audit_logs(raw_lines: List[str]) -> Dict[str, object]:
    lines_out: List[str] = []
    totals: Dict[str, int] = {}
    loads = json.loads
    match_code = _CODE_RE.match
    digest_len = 0
    for raw in raw_lines:
        rec = loads(raw)
        if not rec.get("ok"): continue
        m = match_code(rec["code"])
        if not m: continue
        sym = m.group(1)
        amt = int(rec["amount"])
        totals[sym] = totals.get(sym, 0) + amt
        line = f"{sym}:{amt}\\n"
        lines_out.append(line)
        digest_len += len(line)
    return {"totals": totals, "digest_len": digest_len, "lines": len(lines_out)}
'''

OPT2_ATTEMPT2 = '''"""Audit log aggregation (Attempt 2: zero-buffer streaming + O(1) ASCII slice check)."""
from __future__ import annotations
import json
from typing import Dict, List

def aggregate_audit_logs(raw_lines: List[str]) -> Dict[str, object]:
    totals: Dict[str, int] = {}
    loads = json.loads
    digest_len = 0
    valid_lines = 0
    for raw in raw_lines:
        rec = loads(raw)
        if not rec.get("ok"): continue
        code = rec["code"]
        n = len(code)
        if n < 7 or n > 11 or code[n - 5] != "-": continue
        sym, num = code[: n - 5], code[n - 4 :]
        if not (sym.isascii() and sym.isalpha() and sym.isupper() and num.isascii() and num.isdigit()): continue
        amt = int(rec["amount"])
        totals[sym] = totals.get(sym, 0) + amt
        digest_len += (n - 5) + len(str(amt)) + 2
        valid_lines += 1
    return {"totals": totals, "digest_len": digest_len, "lines": valid_lines}
'''

OPT3_NOISE = '''"""Fee calculation module (cosmetic local binding — rejected as sub-15% noise)."""
from __future__ import annotations
from typing import List

def compute_batch_fees(amounts: List[float], rate: float = 0.0025) -> float:
    total = 0.0
    rnd = round
    for amt in amounts:
        total += rnd(amt * rate, 4)
    return rnd(total, 2)
'''


def run(args: list[str], cwd: Path) -> str:
    res = subprocess.run(
        [sys.executable, "-B", str(VP_SCRIPT), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    if res.stdout:
        print(res.stdout.rstrip())
    if res.returncode != 0:
        if res.stderr:
            print(res.stderr.rstrip(), file=sys.stderr)
        raise SystemExit(res.returncode)
    return res.stdout


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="veriperf_demo_"))
    try:
        shutil.copytree(EXAMPLE_DIR / "analytics", work / "analytics", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(EXAMPLE_DIR / "tests", work / "tests", ignore=shutil.ignore_patterns("__pycache__"))

        print("=== Phase 0-1: Init & Hotspot Scan ===")
        s_rel = json.loads(run(["init", "--root", str(work)], work).strip())["S"]
        run(["hotspot-scan", "--root", str(work), "--s", s_rel], work)

        benches = work / s_rel / "benches"
        vp_dir = str(VP_SCRIPT.parent)
        (benches / "bench_opt1.py").write_text(BENCH_OPT1.replace("__VP_DIR__", vp_dir), encoding="utf-8")
        (benches / "bench_opt2.py").write_text(BENCH_OPT2.replace("__VP_DIR__", vp_dir), encoding="utf-8")
        (benches / "bench_opt3.py").write_text(BENCH_OPT3.replace("__VP_DIR__", vp_dir), encoding="utf-8")

        print("\n=== Phase 2: Baseline Capture ===")
        for tid in ("opt1", "opt2", "opt3"):
            run(["bench-run", "--s", s_rel, "--id", tid, "--phase", "baseline", "--", sys.executable, f"{s_rel}/benches/bench_{tid}.py"], work)

        print("\n=== Phase 3-4: Sandbox Tuning + Dual Gate Check + Auto-Rollback ===")
        run(["sandbox", "--s", s_rel, "--mode", "opt"], work)
        sb = work / s_rel / "sandbox-opt"
        tc = f"{sys.executable} -m unittest discover -s tests -q"

        run(["sandbox", "--s", s_rel, "--mode", "save", "--id", "opt1"], work)
        (sb / "analytics" / "reconcile.py").write_text(OPT1_CODE, encoding="utf-8")
        run(["verify-opt", "--s", s_rel, "--id", "opt1", "--auto-rollback", "--test-cmd", tc], work)

        run(["sandbox", "--s", s_rel, "--mode", "save", "--id", "opt2"], work)
        (sb / "analytics" / "audit_agg.py").write_text(OPT2_ATTEMPT1, encoding="utf-8")
        print("\n-- OPT-2 Attempt 1 (Expected Rejection & Auto-Rollback) --")
        run(["verify-opt", "--s", s_rel, "--id", "opt2", "--auto-rollback", "--test-cmd", tc], work)

        print("\n-- OPT-2 Attempt 2 (Zero-Buffer Streaming + Interleaved A/B) --")
        (sb / "analytics" / "audit_agg.py").write_text(OPT2_ATTEMPT2, encoding="utf-8")
        run(["verify-opt", "--s", s_rel, "--id", "opt2", "--rerun", "--auto-rollback", "--test-cmd", tc], work)

        run(["sandbox", "--s", s_rel, "--mode", "save", "--id", "opt3"], work)
        (sb / "analytics" / "fees.py").write_text(OPT3_NOISE, encoding="utf-8")
        print("\n-- OPT-3 Micro-Tweak (Expected Rejection & Auto-Rollback) --")
        run(["verify-opt", "--s", s_rel, "--id", "opt3", "--auto-rollback", "--test-cmd", tc], work)

        print("\n=== Phase 5: Summary, Compact Diff & Cleanliness Check ===")
        run(["summary", "--s", s_rel], work)
        run(["sandbox", "--s", s_rel, "--mode", "diff"], work)
        run(["sandbox", "--s", s_rel, "--mode", "clean"], work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
