# VeriPerf ⚡📊

[![Agent Skills Standard](https://img.shields.io/badge/Agent%20Skills-Compliant-blue.svg)](https://agentskills.io)
[![CI](https://github.com/lufus4826-eng/veriperf/actions/workflows/ci.yml/badge.svg)](https://github.com/lufus4826-eng/veriperf/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/Version-1.1.1-purple.svg)](https://github.com/lufus4826-eng/veriperf/releases/tag/v1.1.1)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platforms](https://img.shields.io/badge/Platforms-Claude%20Code%20%7C%20Antigravity%20%7C%20Cursor%20%7C%20Windsurf-green.svg)](#installation)
[![OS](https://img.shields.io/badge/OS-Windows%20%7C%20macOS%20%7C%20Linux-orange.svg)](#supported-stacks--language-adapters)
[![Zero Dependencies](https://img.shields.io/badge/Dependencies-Python%203%20Stdlib%20Only-teal.svg)](#prerequisites)
[![VeriHunt Ecosystem](https://img.shields.io/badge/Ecosystem-VeriHunt-indigo.svg)](https://github.com/lufus4826-eng/verihunt)

> **VeriPerf — Statistical Performance Profiling & Dual-Gate Sandbox Optimization Agent Skill.**  
> Stop letting AI agents optimize code by visual guesswork. VeriPerf scans for algorithmic and I/O bottlenecks, measures **Small / Medium / Large** workloads across `3–5` warm-up and `15–30` timed iterations (`p50`, `p95`, Peak Memory), tunes code inside an isolated sandbox, and **automatically rejects and rolls back** any change that breaks tests, alters output hashes, blows up peak memory, or gains less than **15%**.

---

## Why VeriPerf?

When you ask an AI coding agent to *"optimize this code"*, four things usually go wrong:
1. **Placebo micro-tweaks:** The agent makes code harder to read for a 1–3% speedup that is indistinguishable from CPU jitter.
2. **RAM blowups disguised as speedups:** Unbounded caching makes code 16% faster while silently spiking peak memory by 500%.
3. **Thermal/CPU drift & silent regressions:** Sequential timing runs suffer from background load or subtly alter float precision and ordering.
4. **Token bloat:** Running 20–30 benchmark iterations raw in the shell floods the agent's context window with thousands of lines of noisy stdout.

**VeriPerf (`v1.1.0`) solves all four using a deterministic Python CLI engine (`scripts/vp.py`) and strict Dual Gate verification:**
- **Gate 1 (Correctness & Canonical Equivalence):** Project tests must pass (`exit=0` with zero broken-harness signals) **and** canonical output checksums (`canonical_hash`, rounding floats and sorting dict keys) must match 100% across `small`, `medium`, and `large` tiers.
- **Gate 2 (15% Speedup + Peak Memory Ceiling):** Primary-tier `p50` must improve by **>= 15.0%**, `p95` tail latency must not regress, and peak memory growth must stay **<= +25.0%** (above a 16 KB noise floor, unless `--allow-mem-tradeoff` is explicitly approved).
- **Counterbalanced `ABBA` Interleaved Re-Verification (`--interleave auto`):** Borderline speedups (`12–18%`) or high-variance runs (`CV > 5%`) automatically trigger interleaved baseline/optimized legs to neutralize CPU thermal throttling.
- **Deterministic Checkpoints & Auto-Rollback:** Per-target sandbox snapshots (`sandbox --mode save|rollback`, `--auto-rollback`) revert rejected edits byte-for-byte without touching previously accepted optimizations.

---

## Verified Benchmark Case Study (`examples/portfolio_bench`)

Tested end-to-end (`warmup=5`, `runs=20`, `MIN_SPEEDUP=15.0%`, `MAX_MEM_GROWTH=25.0%`) via [`examples/portfolio_bench/run_demo.py`](examples/portfolio_bench/run_demo.py) — see the generated [`sample-perf-report.md`](examples/portfolio_bench/sample-perf-report.md):

| Target | Optimization Tested | Primary Tier | Baseline `p50` | Optimized `p50` | `p50` Speedup | `p95` Change | Peak Memory | Hash | Dual Gate Verdict |
|---|---|---|---:|---:|---:|---:|---:|---|---|
| **OPT-1** (`reconcile_trades`) | Replaced `O(N*M)` nested scan with `O(N+M)` in-place dict index | `large` | `144.637 ms` | `1.802 ms` | **+98.8% (80.25x)** | +98.7% | +16.0% (`<= +25%`) | MATCH | **ACCEPTED** |
| **OPT-2** *(Attempt 1)* | Hoisted regex + buffered strings in `list.append` | `large` *(interleaved)* | `25.410 ms` | `21.170 ms` | +16.7% | **-33.4% (worse)** | **+537.8% (blowup)** | MATCH | **REJECTED + Auto-Rolled Back** |
| **OPT-2** *(Attempt 2)* | Streaming length accounting + `O(1)` ASCII slice validation | `large` *(interleaved)* | `25.601 ms` | `21.339 ms` | **+16.6% (1.20x)** | +6.3% | **-95.5% saved** (`70.2 -> 3.1 KB`) | MATCH | **ACCEPTED** |
| **OPT-3** (`compute_batch_fees`) | Local variable micro-binding (`rnd = round`) | `large` | `41.414 ms` | `40.139 ms` | **+3.1%** | +2.8% | -9.5% | MATCH | **REJECTED (`<15%`) + Auto-Rolled Back** |

> **Key takeaway from OPT-2:** On Python 3.13, a textbook `list.append` buffer refactor triggered automatic `ABBA` interleaved verification (because its initial `+16.3%` gain was near the 15% threshold), revealing a `-33.4%` `p95` tail regression and `+537.8%` peak memory blowup. VeriPerf automatically rolled it back and drove a zero-buffer streaming optimization that cut memory by **95.5%**.

---

## Try the 1-Command Live Demo

Clone the repo and run the full 5-phase VeriPerf v1.1 workflow (including automatic interleaved verification and checkpoint rollback) in one command on an isolated temporary workspace:

```bash
git clone https://github.com/lufus4826-eng/veriperf.git
cd veriperf
python examples/portfolio_bench/run_demo.py
```

---

## Quick Start

### Prerequisites
- **Python 3.9+** available on `PATH` as `python` (used by `scripts/vp.py`; **zero external `pip` packages required**).
- Your target project's native runtime/toolchain (Python, Go, Node.js, Java/Kotlin, .NET, or Rust).

### Installation

#### 0. Full VeriHunt Suite via VeriPilot (Recommended — One-Command Install)
Install `VeriPilot` + `VeriPR` + `VeriHunt` + `VeriSec` + `VeriPerf` together across 6 agent platforms:
```bash
git clone https://github.com/lufus4826-eng/veripilot.git ~/.gemini/config/skills/veripilot
python ~/.gemini/config/skills/veripilot/scripts/vpilot.py install --platform antigravity   # or --platform claude | --platform all
```

#### 1. Claude Code
```bash
# User-global installation (Recommended)
git clone https://github.com/lufus4826-eng/veriperf.git ~/.claude/skills/veriperf

# Or project-local installation
git clone https://github.com/lufus4826-eng/veriperf.git .claude/skills/veriperf
```

#### 2. Google Antigravity / Gemini CLI
```bash
# Linux / macOS
git clone https://github.com/lufus4826-eng/veriperf.git ~/.gemini/config/skills/veriperf

# Windows (PowerShell)
git clone https://github.com/lufus4826-eng/veriperf.git "$HOME\.gemini\config\skills\veriperf"
```

#### 3. Cursor / Windsurf / Roo Code / Cline / Other Agent Runtimes
```bash
git clone https://github.com/lufus4826-eng/veriperf.git .agents/skills/veriperf
```

### Example Prompts
```text
/veriperf profile and optimize bottlenecks in this repository with before/after benchmarks
/veriperf hunt O(N^2) loops, N+1 queries, and excessive memory allocations in src/billing
/veriperf benchmark and speed up the order reconciliation pipeline without breaking tests
```

---

## Methodology & 5-Phase Workflow

```mermaid
flowchart TD
    A["Phase 0–1: vp.py init + hotspot-scan + Workload Tiers (S/M/L)"] --> B["Phase 2: Write High-Res Harnesses + vp.py bench-run --phase baseline"]
    B --> C["Phase 3: sandbox --mode opt + save --id optN + Tune in sandbox-opt"]
    C --> D["Phase 4: vp.py verify-opt --interleave auto --auto-rollback (Tests, Hash, >=15% p50, p95, <=25% Mem)"]
    D -- "Borderline 12-18% / CV>5%" --> D1["Counterbalanced ABBA Interleaved Re-Check"]
    D1 --> D
    D -- "REJECTED" --> F["Auto-Restored via Checkpoint + Logged in Section 4"]
    D -- "ACCEPTED" --> G["Checkpoint _last_accepted Updated & Diff Retained"]
    F --> H["Phase 5: vp.py summary + sandbox --mode diff + sandbox --mode clean -> S/perf-report.md"]
    G --> H
```

| Phase | Purpose | Enforcement Mechanism |
|---|---|---|
| **Phase 0–1: Hotspot Discovery** | Locate bottlenecks & define Small / Medium / Large dataset sizes | `vp.py init` + `vp.py hotspot-scan` (`<=35` lines) + `resources/hotspot-patterns.md` |
| **Phase 2: Baseline Capture** | Capture statistically stable pre-tuning metrics without modifying host code | `vp.py bench-run --phase baseline` (`warmup >= 3`, `runs >= 15`, `p50`, `p95`, `peak_kb`, `result_hash`) |
| **Phase 3: Sandbox Tuning** | Snapshot checkpoint & implement algorithmic, batching, or streaming fixes safely | `vp.py sandbox --mode opt` + `save --id optN` inside `.bughunt/perf-*/sandbox-opt/` |
| **Phase 4: Dual Gate Check** | Prove equivalence, `>= 15%` speedup, no `p95` regression, & `<= +25%` memory ceiling | `vp.py verify-opt` with `--interleave auto` (`ABBA`) and `--auto-rollback` |
| **Phase 5: Benchmark Report** | Deliver audit-ready Before/After report and clean temporary sandboxes | `vp.py summary` + `sandbox --mode diff` + `sandbox --mode clean` -> `S/perf-report.md` |

---

## `scripts/vp.py` CLI Reference

| Subcommand | Description |
|---|---|
| `init [--root .]` | Creates `.bughunt/perf-<YYYYMMDD-HHMMSS>/{benches,logs,tmp}` + `.cache`, captures `fs-before.txt`, initializes `worklog.md`, and outputs 1-line JSON `{S, SNAME, CACHE, ROOT, snapshot}`. |
| `hotspot-scan [--root .] [--scope dir] [--s S]` | Runs Python AST + multi-language structural scanning for `NESTED_LOOP`, `N_PLUS_ONE_IO`, `UNBOUNDED_MEMORY_READ`, and `LOOP_ALLOC_OR_RECOMPILE` (excluding test files), printing a ranked `<=35`-line report. |
| `bench-run --s S --id ID --phase baseline\|opt [--warmup 5] [--runs 20] [--tier all] -- <cmd>` | Enforces `warmup >= 3` and `runs >= 15`, parses `VERIPERF_METRIC:` JSON lines (or times subprocesses as fallback), computes `p50`, `p95`, `mean`, `CV%`, `peak_kb`, and `result_hash`, and prints a **<=10-line** digest. |
| `verify-opt --s S --id ID [--min-speedup 15.0] [--max-mem-growth 25.0] [--allow-mem-tradeoff] [--interleave auto] [--auto-rollback] [--test-cmd "..."]` | Runs inside `S/sandbox-opt/`, executes `--test-cmd` (checking exit `0` + broken-harness signals), verifies `result_hash` parity, auto-triggers counterbalanced `ABBA` interleaving on `12–18%` / `CV > 5%` runs, enforces `p50 >= +15%`, `p95 >= 0%`, and `mem_growth <= +25%`, and rolls back rejected edits cleanly (`<=12`-line digest). |
| `sandbox --s S --mode create\|opt\|save\|rollback\|diff\|clean [--id ID] [--force]` | Clones project into `S/sandbox-opt/` (preserving `vendor/`, excluding `.git`/caches/build dirs), saves/restores per-target checkpoints (`save`/`rollback`), prints `<=8,000`-char unified diffs (`diff`), or removes sandboxes and verifies `fs-before.txt` vs `fs-after.txt` (`clean`). |
| `log --s S [--status "..."] [--entry "..."]` | Atomically updates `Status:` on line 1 of `S/worklog.md` and appends timestamped 1-line entries without rewriting the file. |
| `summary --s S` | Generates ready-to-embed Markdown comparison tables from session JSON logs (`ACCEPTED` and `REJECTED` targets) without reading raw logs. |
| `canonical-hash [--float-digits 6] [--sig-figs N] [--unordered-lists] [--json-file PATH \| --json-str STR]` | Computes deterministic float-rounded (or scientific significant-figure) / key-sorted SHA-256[:16] digests via `stdin`, `--json-file` (safe for large outputs on Windows), or `--json-str`. |

### `VERIPERF_METRIC:` Harness Protocol
Language harnesses write one JSON line per workload tier to stdout:
```text
VERIPERF_METRIC:{"tier":"large","warmup":5,"runs":20,"samples_ns":[1612400,1598200,...],"peak_kb":317.2,"result_hash":"8da897776a12bc90"}
```
`vp.py` sets `VERIPERF_TARGET_ROOT` (`ROOT` during `baseline`, `S/sandbox-opt` during `opt`), `VERIPERF_WARMUP`, `VERIPERF_RUNS`, and `VERIPERF_TIER` automatically so the same benchmark script measures both baseline and sandbox-tuned code identically.

---

## Supported Stacks & Language Adapters

| Language | Monotonic Timer | Memory Metric | Isolation & Build Strategy |
|---|---|---|---|
| **Python** | `time.perf_counter_ns()` | `tracemalloc` peak heap (post-timing pass) | `VERIPERF_TARGET_ROOT` + `PYTHONPATH`, `PYTHONDONTWRITEBYTECODE=1` |
| **Go** | `time.Now()` / `time.Since()` | `runtime.ReadMemStats` `TotalAlloc` delta | `S/sandbox[-opt]/`, `go test -count=1`, `GOCACHE`, `GOPROXY=off` |
| **Node.js / TS** | `process.hrtime.bigint()` | `process.memoryUsage().heapUsed` | `pathToFileURL(VERIPERF_TARGET_ROOT)` dynamic `import()` (ESM, CJS & TS), `npm_config_offline=true` |
| **Java / Kotlin (JVM)** | `System.nanoTime()` | `Runtime` heap / `MemoryMXBean` | `S/sandbox[-opt]/`, `mvn -o` / `gradlew --offline` |
| **C# (.NET)** | `Stopwatch.GetTimestamp()` | `GC.GetAllocatedBytesForCurrentThread()` | `S/sandbox[-opt]/`, `dotnet test -c Release --no-restore` |
| **Rust** | `std::time::Instant` + `black_box` | Tracking allocator / RSS | `S/sandbox[-opt]/`, `cargo test --release --offline --locked` |

---

## Repository Structure & Session Deliverables

### Repository Layout
```text
veriperf/
├── SKILL.md                          # Master 5-phase workflow & token guardrails
├── README.md                         # Documentation & benchmark methodology
├── CONTRIBUTING.md                   # Contributor & adapter authoring guide
├── LICENSE                           # MIT License
├── scripts/
│   └── vp.py                         # Zero-dependency stdlib Python CLI engine (v1.1)
├── tests/
│   └── test_vp_cli.py                # Cross-platform unit & integration test suite
├── examples/
│   └── portfolio_bench/              # 1-command runnable demo + sample-perf-report.md
└── resources/
    ├── hotspot-patterns.md           # Bottleneck pattern & remediation catalog
    ├── report-template.md            # English 6-section report template (Phase 5 lazy-load)
    ├── report-template-th.md         # Thai 6-section report template (Phase 5 lazy-load)
    └── lang/                         # Language-specific timing & memory adapters
        ├── 00-common.md              # VERIPERF_METRIC protocol, canonical hash & stats rules
        ├── python.md
        ├── go.md
        ├── js-ts.md
        ├── jvm.md
        ├── dotnet.md
        └── rust.md
```

### Session Output (`.bughunt/perf-<YYYYMMDD-HHMMSS>/`)
```text
.bughunt/perf-20261116-205732/
├── perf-report.md        # Final 6-section statistical report & verified diffs
├── worklog.md            # Append-only progress journal
├── benches/              # Reproducible multi-tier benchmark harnesses
└── logs/                 # Structured tier JSON metrics, verify verdicts & fs snapshots
```
> **Note:** Add `.bughunt/` to `.gitignore` or `.git/info/exclude` — session logs and benchmark artifacts should not be committed into your target repository.

---

## VeriHunt Ecosystem

VeriPerf pairs directly with **[VeriPilot (VeriHunt Suite)](https://github.com/lufus4826-eng/veripilot)** and the VeriHunt suite using the shared `.bughunt/` workspace convention:
- Use **`/veripilot`** to auto-triage, route in strict Causal Order (`veripr -> verihunt -> verisec -> veriperf`), and chain patches over a Cumulative Overlay workspace.
- Use **`/veripr`** right before commit/PR to catch 1-hop caller ripples and silent API `schema_hash` drift.
- Use **`/verihunt`** to hunt and prove **correctness, edge-case, and concurrency bugs** (`FAIL` -> `PASS` in `sandbox-fix`).
- Use **`/verisec`** to audit **business-logic & authorization flaws** (`IDOR/BOLA`, `Missing Auth Guard`, `Mass Assignment`, `Race Double-Spend`) with 100% network-isolated PoCs and secret redaction.
- Use **`/veriperf`** once code is functionally sound and secure to eliminate **`O(N^2)` loops, N+1 I/O, and memory bloat** (`>= 15%` proven speedup in `sandbox-opt`).

---

## Contributing & License

- Contributions, new language adapters, and AST/scanner improvements are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md).
- Licensed under the **MIT License** — see [LICENSE](LICENSE).
