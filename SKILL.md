---
name: veriperf
description: Use when asked to find performance bottlenecks, profile execution speed or memory usage, optimize slow code, fix N+1 queries or O(N^2) loops, or benchmark before/after speedups across Python, Go, Node.js/TS, Java/Kotlin, C#, or Rust with statistical proof. (ใช้เมื่อผู้ใช้ขอให้หาคอขวด จูนความเร็ว ลดการใช้แรม แก้โค้ดอืด หรือทำ benchmark วัดผลความเร็วก่อนและหลังแก้ด้วยสถิติจริง — performance audit, optimize speed, bottleneck hunt, profiling, benchmark)
---

# VeriPerf

Goal: Find performance bottlenecks **empirically proven via statistical benchmarking** (never visual guessing). Deliver `S/perf-report.md` with reproducible harnesses and sandbox-verified unified diffs without modifying host files before explicit user approval.

**Supreme Principle (Dual Gate):** No optimization is valid on intuition alone. Every candidate MUST pass **both** gates:
1. **Correctness Gate:** `0` existing project test failures AND 100% identical canonical output checksum (`result_hash`) across all tiers before and after.
2. **Speedup & Memory Gate:** `>= 15.0%` `p50` improvement on the primary tier, zero `p95` regression, and peak memory growth `<= +25.0%` (unless user explicitly authorizes `--allow-mem-tradeoff`). Borderline (`12–18%`) or high-variance (`CV > 5%`) candidates automatically re-run counterbalanced **ABBA interleaved** checks (`--interleave auto`), and any rejected edit is rolled back deterministically via `vp.py`.

## Default Parameters
`WARMUP=5` (`3–5`, hard min `3`) | `RUNS=20` (`15–30`, hard min `15`) | `MIN_SPEEDUP=15.0%` (hard floor) | `MAX_MEM_GROWTH=25.0%` | `INTERLEAVE=auto` | `TIERS=small,medium,large` | `S=.bughunt/perf-<YYYYMMDD-HHMMSS>/` | `VP=<skill_dir>/scripts/vp.py` | **Language:** Auto-match user prompt language (Thai or English; keep standard technical terms like *p50*, *p95*, *N+1 query*, *hash map*, *cache hit*, *GC* in English) for chat, `S/worklog.md`, and `S/perf-report.md`.

## Token Efficiency Rules (Mandatory)
1. **Grep-First & Ranged Reads:** Use `vp.py hotspot-scan` or `rg`/`grep` first, then `read_file` with `StartLine`/`EndLine` (`<=150` lines/call). Never dump whole files or re-read unchanged lines, and **never** read `S/logs/*.log` or `S/logs/*.json` (neither via `read_file` nor `Get-Content`/`cat`).
2. **CLI-First (`scripts/vp.py`):** Use `python "<VP>"` (`init`, `hotspot-scan`, `bench-run`, `verify-opt`, `sandbox`, `log`, `summary`). Rely on `<=10`-line digests and `vp.py summary` Markdown tables.
3. **Batch & Turn-Fusion:** Never spend a standalone turn calling only `vp.py log` or create `S/tmp/*.md`. Fuse `vp.py log --entry "..."` into the next turn's benchmark writes, sandbox actions, or Phase 5 report prep.
4. **Lazy-Load Heavy Templates:** Read `resources/report-template-th.md` (Thai) or `resources/report-template.md` (English) **ONLY in Phase 5** (never load both!). Load ONLY the matching `resources/lang/<lang>.md`.

## Reference Files (Lazy-Load by Phase)
- `scripts/vp.py` — Zero-dependency statistical benchmark & sandbox CLI.
- `resources/hotspot-patterns.md` — Phase 0–1: algorithmic, N+1, I/O, and memory bottleneck catalog.
- `resources/lang/00-common.md` — Phase 0–1: `VERIPERF_METRIC` protocol, isolation, statistical rules.
- `resources/lang/<lang>.md` — Phase 0–1: load **only** matching language (`python`, `go`, `js-ts`, `jvm`, `dotnet`, `rust`).
- `resources/report-template.md` — **Phase 5 ONLY (English)**.
- `resources/report-template-th.md` — **Phase 5 ONLY (Thai)**.

## Core Rules & Isolation (`.bughunt/`)
1. Execute Phases `0–1 -> 2 -> 3 -> 4 -> 5` sequentially; never skip Dual Gate checks.
2. All agent files live strictly inside `.bughunt/.cache/` and `S/` (`benches/`, `logs/`, `tmp/`, `sandbox-opt/`, `worklog.md`, `perf-report.md`).
3. **NEVER** modify host source files, configs, dependencies, or lockfiles before explicit user approval; **NEVER** install/download packages; **NEVER** run `git checkout|restore|reset|clean`.
4. Physical `S/perf-report.md` is mandatory (chat summaries alone do not count).

---

## Phase 0–1 — Hotspot Discovery
1. **Init + Scan (Single Turn):** Run chained command:
   - `python "<VP>" init --root .` -> capture `{S, SNAME, CACHE, ROOT}`
   - `python "<VP>" hotspot-scan --root .` (`<=35`-line ranked report of `NESTED_LOOP`, `N_PLUS_ONE_IO`, `UNBOUNDED_MEMORY_READ`, `LOOP_ALLOC_OR_RECOMPILE`)
   In the same turn (or right after stack detection), load `resources/hotspot-patterns.md`, `resources/lang/00-common.md`, and ONLY matching `resources/lang/<lang>.md`.
2. **Confirm Target Functions & Workload Tiers:**
   - Read candidate hotspots via ranged `read_file` (`<=150`L), verify true hot-path impact vs false positives, and also run baseline project tests once if needed (`--tid` or quick check) to confirm green baseline.
   - Select top candidate targets (`opt1`, `opt2`, ...; display `OPT-1`, `OPT-2`).
   - Define concrete dataset scales for **3 tiers** (`small`, `medium`, `large`) that produce measurable runtime (`>= 5–10 ms` on `large` if possible so clock jitter doesn't dominate).
   - Output concise Phase 1 target summary in chat (matching prompt language).

## Phase 2 — Baseline Capture
1. **Fuse Log + Batch Write Harnesses (One Turn):**
   - Run `python "<VP>" log --s "$S" --status "Phase 2 - Baseline Capture" --entry "Targets: opt1 (..), opt2 (..); Tiers: S/M/L=..."`
   - **In the same turn**, write high-resolution benchmark scripts (`S/benches/bench_opt1.<ext>`, ...) in parallel using the language adapter template:
     - Respect `VERIPERF_TARGET_ROOT` env var (defaults to `ROOT` in baseline, auto-switched to `S/sandbox-opt` in Phase 4).
     - Perform `3–5` warm-up runs (`VERIPERF_WARMUP`, default `5`) then `15–30` timed runs (`VERIPERF_RUNS`, default `20`) per tier (`small`, `medium`, `large`).
     - Trigger GC cleanly before timed loops when applicable, measure Peak Memory (`peak_kb`), compute deterministic `result_hash` of the function's output, and print one `VERIPERF_METRIC:{...}` JSON line per tier.
2. **Execute Baseline Benchmarks:** Run `python "<VP>" bench-run --s "$S" --id optN --phase baseline --warmup 5 --runs 20 -- <cmd>` (chain all targets in one turn if fast). Record `p50`, `p95`, `peak_kb`, and `result_hash` from the `<=10`-line digest.

## Phase 3 — Sandbox Tuning
1. **Create Sandbox:** Run `python "<VP>" sandbox --s "$S" --mode opt` (copies `ROOT` into `S/sandbox-opt/`), fused with `vp.py log --status "Phase 3 - Sandbox Tuning" --entry "Baseline captured: opt1 p50=..., ..."`.
2. **Checkpoint Before Each Target & Tune in `S/sandbox-opt/` ONLY:**
   - Before editing target `optN`, snapshot the current `S/sandbox-opt/` state via `python "<VP>" sandbox --s "$S" --mode save --id optN` (so any rejected edit can be rolled back byte-for-byte without touching earlier accepted edits).
   - Replace `O(N^2)` nested scans with hash maps/sets (`O(N)`), batch N+1 calls, stream large files, hoist regex/allocations, or use bounded caches (`@lru_cache(maxsize=...)` — watch the `+25%` peak memory ceiling).

## Phase 4 — Dual Gate Check
1. **Run `verify-opt` with `--auto-rollback` per Target:**
   ```text
   python "<VP>" verify-opt --s "$S" --id optN --min-speedup 15.0 --max-mem-growth 25.0 --interleave auto --auto-rollback --test-cmd "<project test cmd>"
   ```
   (`vp.py` runs inside `S/sandbox-opt/` with `VERIPERF_TARGET_ROOT=S/sandbox-opt`, runs `--test-cmd`, checks `result_hash` parity, auto-triggers counterbalanced `ABBA` interleaved re-verification on `12–18%` / `CV > 5%` runs, checks `p50 >= +15%`, `p95 >= 0%`, and `mem_growth <= +25%`, saves `_last_accepted` on `ACCEPTED`, and auto-restores the checkpoint on `REJECTED`).
2. **Enforce Iron Rule:**
   - `ACCEPTED`: Proceed to next target (`_last_accepted` checkpoint updated automatically).
   - `REJECTED_TEST_FAILURE` / `REJECTED_OUTPUT_MISMATCH` / `REJECTED_MEMORY_BLOWUP`: `--auto-rollback` restores `S/sandbox-opt/` cleanly (never manually hand-edit files back!). Retry up to 2 alternative approaches if promising, or leave reverted and record in Section 4.
   - `REJECTED_INSUFFICIENT_GAIN (<15%)` or `REJECTED_P95_REGRESSION`: Leave reverted via `--auto-rollback` and record in Section 4.

## Phase 5 — Benchmark Report & Cleanliness
1. **Single Fused Prep Turn:** Run one chained shell command:
   ```text
   python "<VP>" summary --s "$S" ; python "<VP>" sandbox --s "$S" --mode diff ; python "<VP>" sandbox --s "$S" --mode clean
   ```
   **AND** in the same turn call `read_file` on **ONLY** `resources/report-template-th.md` (Thai) or `resources/report-template.md` (English).
2. **Write `S/perf-report.md` & Log (Single Turn):** Populate the loaded template completely using `vp.py summary` tables and `sandbox --mode diff` unified diffs (all 6 sections; `"None"`/`"ไม่มี"` if empty), fused with `python "<VP>" log --s "$S" --status "Phase 5 Complete" --entry "Report finalized; sandbox cleaned."`.
3. **Handoff:** Summarize Before/After numbers (both `ACCEPTED` and `REJECTED` candidates), `cleanliness` status, and `S/perf-report.md` path in chat (matching prompt language), and ask which `ACCEPTED` optimizations to apply to host code.

## Post-Report Host Application (Explicit Approval Only)
Apply approved `ACCEPTED` diffs to host files -> re-run project tests + optional confirmation `bench-run` -> append Section 6 `"Applied Optimizations Log"` in `S/perf-report.md`.
