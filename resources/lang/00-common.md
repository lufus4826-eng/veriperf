# 00 — Common Statistical Benchmarking Protocol & Isolation

Used with `resources/lang/<lang>.md`. Resolve `<VP>` as `<skill_dir>/scripts/vp.py`. Execute commands from `ROOT`.

## 1. CLI Workflow (`scripts/vp.py`)
`vp.py` auto-redirects caches to `.bughunt/.cache/`, sets `VERIPERF_TARGET_ROOT` (`ROOT` for `--phase baseline`, `S/sandbox-opt` for `--phase opt`), and injects `VERIPERF_WARMUP`, `VERIPERF_RUNS`, `VERIPERF_TIER`:
- `python "<VP>" init [--root .]` -> creates `S/{benches,logs,tmp}` + `.cache`, writes `fs-before.txt`, inits `worklog.md`, prints JSON `{S, SNAME, CACHE, ROOT, snapshot}`.
- `python "<VP>" hotspot-scan [--root .] [--scope dir] [--s "$S"]` -> `<=35`-line ranked report of `NESTED_LOOP`, `N_PLUS_ONE_IO`, `UNBOUNDED_MEMORY_READ`, `LOOP_ALLOC_OR_RECOMPILE`.
- `python "<VP>" bench-run --s "$S" --id <ID> --phase baseline|opt [--warmup 5] [--runs 20] [--tier all] -- <cmd...>` -> enforces `warmup >= 3`, `runs >= 15`, saves `S/logs/<id>_<phase>.json`, emits `<=10`-line digest (`p50`, `p95`, `mean`, `cv%`, `peak_kb`, `hash`).
- `python "<VP>" verify-opt --s "$S" --id <ID> [--min-speedup 15.0] [--opt-bench-cmd "..."] [--test-cmd "..."]` -> runs Gate 1 (`test-cmd` exit 0 + no broken test signals + exact `result_hash` match per tier) AND Gate 2 (`p50_speedup_pct >= 15.0%`, `p95_speedup_pct >= 0.0%`). Saves `S/logs/<id>_verify.json`.
- `python "<VP>" sandbox --s "$S" --mode opt|diff|clean [--force]` -> manages `S/sandbox-opt/`, writes compact unified diff (`<=8,000` chars), and checks cleanliness (`fs-before.txt` vs `fs-after.txt`).
- `python "<VP>" log --s "$S" [--status "..."] [--entry "..."]` -> atomic worklog update (always fuse with next turn).
- `python "<VP>" summary --s "$S"` -> emits ready-to-use Markdown tables for Phase 5.

## 2. `VERIPERF_METRIC:` Harness Protocol
Benchmarks should emit **one JSON line per tier** prefixed by `VERIPERF_METRIC:` to stdout:
```json
VERIPERF_METRIC:{"tier":"small","warmup":5,"runs":20,"samples_ns":[1240100,...],"peak_kb":142.5,"result_hash":"a1b2c3d4e5f60718"}
```
- **Required fields:** `tier` (`small|medium|large|default`), `warmup` (`>=3`), `runs`/`samples_ns` (`len >= 15`, integer/float nanoseconds; `samples_ms` also accepted), `peak_kb` (`>=0` float KB), `result_hash` (deterministic digest of target return value, ensuring Gate 1 catches subtle output drift).
- **Subprocess Fallback (Mode B):** If a CLI command prints no `VERIPERF_METRIC:` lines, `vp.py bench-run` runs it `W` warm-up + `R` timed subprocess iterations, measuring wall time and stdout SHA-256. Prefer Mode A (`VERIPERF_METRIC:`) to eliminate process-startup noise!

## 3. Statistical & Anti-Illusion Rules
1. **Minimum Samples:** Always `warmup >= 3` (recommend `5`) and `runs >= 15` (recommend `20–30`).
2. **Workload Sizing:** Choose `small`, `medium`, `large` sizes so `large` baseline `p50` is ideally `>= 5–20 ms` (prevents timer resolution & OS scheduling jitter from distorting percentages).
3. **Fresh Input Isolation:** If the target mutates inputs in-place, build/clone fresh input **outside** the timed section (or measure clean copy overhead identically in baseline and opt).
4. **No Dead-Code Elimination:** Always consume/hash the function's return value into `result_hash`.
5. **15% Threshold:** Any candidate with `< 15.0%` `p50` improvement or `p95` regression on the primary tier is **REJECTED** and reverted from `S/sandbox-opt/`.
6. **Offline / No Package Installs:** Never install third-party benchmark packages (`pytest-benchmark`, `hyperfine`, `criterion`, `BenchmarkDotNet`) unless already present in project deps. Use built-in high-resolution clocks in the language adapters.
