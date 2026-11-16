# Rust Performance Adapter

## 1. High-Resolution Clock & Memory APIs
- **Timer:** `std::time::Instant::now()` / `elapsed().as_nanos()` inside `--release` builds (`cargo test --release --offline --locked`).
- **Memory:** Optional lightweight tracking `GlobalAlloc` atomic peak counter inside the benchmark binary/integration test (`S/sandbox[-opt]/tests/veriperf_opt1.rs`), or `0.0` if custom global allocator isn't compatible with crate setup.
- **Anti-DCE:** Wrap inputs and outputs with `std::hint::black_box(...)` so LLVM cannot fold or eliminate computations!

## 2. Executing Benchmarks & Correctness Tests
- **Phase 2 Baseline (in `S/sandbox/`):**
  `python "<VP>" bench-run --s "$S" --id opt1 --phase baseline --cwd "$S/sandbox" -- cargo test --release --offline --locked --test veriperf_opt1 -- --nocapture`
- **Phase 4 Dual Gate (in `S/sandbox-opt/`):**
  `python "<VP>" verify-opt --s "$S" --id opt1 --cwd "$S/sandbox-opt" --opt-bench-cmd "cargo test --release --offline --locked --test veriperf_opt1 -- --nocapture" --test-cmd "cargo test --offline --locked --lib"`

## 3. Harness Template (`S/sandbox[-opt]/tests/veriperf_opt1.rs`)
```rust
use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::hint::black_box;
use std::time::Instant;

#[test]
fn veriperf_opt1() {
    let warmup: usize = std::env::var("VERIPERF_WARMUP").ok().and_then(|s| s.parse().ok()).unwrap_or(5);
    let runs: usize = std::env::var("VERIPERF_RUNS").ok().and_then(|s| s.parse().ok()).unwrap_or(20);

    for (tier, n) in [("small", 200usize), ("medium", 2_000), ("large", 10_000)] {
        let input = build_input(n);
        let mut last = Default::default();
        for _ in 0..warmup {
            last = black_box(target_fn(black_box(&input)));
        }
        let mut samples_ns = Vec::with_capacity(runs);
        for _ in 0..runs {
            let t0 = Instant::now();
            last = black_box(target_fn(black_box(&input)));
            samples_ns.push(t0.elapsed().as_nanos() as u64);
        }
        let mut h = DefaultHasher::new();
        last.hash(&mut h);
        let samples_str = samples_ns.iter().map(|v| v.to_string()).collect::<Vec<_>>().join(",");
        println!(
            "VERIPERF_METRIC:{{\"tier\":\"{tier}\",\"warmup\":{warmup},\"runs\":{runs},\"samples_ns\":[{samples_str}],\"peak_kb\":0.0,\"result_hash\":\"{:016x}\"}}",
            h.finish()
        );
    }
}
```

## 4. Rust Optimization Idioms & Anti-Illusion Traps
- **Always benchmark `--release`:** Debug builds lack LLVM optimizations and overstate bounds-check/iterator costs by 10–50x.
- **Output stability:** `DefaultHasher` is randomized per-process in Rust! If comparing baseline vs opt across separate processes (`Phase 2` vs `Phase 4`), hash deterministically using FNV-1a (64-bit) or SHA-256 rather than `DefaultHasher`!
- **Idiomatic wins:** `HashMap`/`HashSet` (or `Vec` pre-allocated with `Vec::with_capacity`), pass `&str`/`&[T]` slices instead of `.clone()`, `BufReader::new(file)` + `read_line(&mut buf)` reusing buffer instead of `fs::read_to_string`, hoist `Regex::new` via `std::sync::OnceLock`.
