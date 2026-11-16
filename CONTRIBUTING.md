# Contributing to VeriPerf

Thank you for your interest in contributing to **VeriPerf** (`veriperf`)!

## Core Philosophy (Do Not Violate)

1. **Statistical Proof Over Intuition:** Every optimization must be backed by warm-up runs (`>= 3`), measured runs (`>= 15`), `p50`/`p95` latency, peak memory, and deterministic output checksums (`result_hash`).
2. **Dual Gate Enforcement:** `scripts/vp.py verify-opt` must never compromise Gate 1 (project test pass + output hash equivalence) or the Gate 2 hard floor (`>= 15.0%` `p50` improvement and zero `p95` regression).
3. **Zero Host Footprint:** Optimizations are edited and verified strictly inside `.bughunt/perf-*/sandbox-opt/`. Host files, lockfiles, and installed environments are never modified before explicit user approval.
4. **Zero External Dependencies:** `scripts/vp.py` must run on **Python 3.9+ standard library only** and work cleanly across **Windows (PowerShell)** and **Linux/macOS (Bash)**.
5. **Token Efficiency:** Keep `SKILL.md` compact, preserve Phase 5 template lazy-loading, and cap `vp.py` CLI digests (`hotspot-scan <= 35` lines, `bench-run <= 10` lines, `verify-opt <= 12` lines).

## How to Contribute

### 1. Adding or Improving a Language Adapter
Language adapters live in `resources/lang/<language>.md`. Every adapter must document:
- High-resolution monotonic clock and memory measurement APIs.
- How Phase 2 (`baseline`) and Phase 4 (`opt`) resolve target code (`VERIPERF_TARGET_ROOT` or `S/sandbox-opt/`) without touching host source files.
- A self-contained harness template emitting `VERIPERF_METRIC:{"tier":...}` lines for `small`, `medium`, and `large` tiers (`warmup >= 3`, `runs >= 15`, `samples_ns`, `peak_kb`, `result_hash`).
- Common anti-illusion traps (JIT warm-up, dead-code elimination, debug vs release builds, profiler overhead inside timed loops).

### 2. Improving `scripts/vp.py`
- Keep `scripts/vp.py` pure Python 3 stdlib.
- Add or update unit/integration tests covering any new hotspot pattern, metric parser behavior, or Dual Gate edge case, and run `python -B scripts/vp.py --help` plus tests before opening a PR.
- Keep `resources/report-template.md` and `resources/report-template-th.md` aligned across all 6 sections if report schemas change.

### 3. Reporting Bugs or False Positives
Please open an issue using the template in `.github/ISSUE_TEMPLATE/bug_report.md` with your runtime version, OS, `vp.py` command, and relevant log excerpt.
