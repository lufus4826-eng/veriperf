# Python Performance Adapter

## 1. High-Resolution Clock & Memory APIs
- **Timer:** `time.perf_counter_ns()` (monotonic nanosecond resolution; never use `time.time()` or `time.clock()`).
- **Memory:** `tracemalloc.start()` -> `tracemalloc.reset_peak()` before the measured workload -> `_, peak_bytes = tracemalloc.get_traced_memory()` -> `peak_kb = peak_bytes / 1024.0`. (Run `tracemalloc` on a dedicated post-timed pass or around the runs so tracing overhead never inflates `samples_ns`!)
- **Target Resolution:** Read `os.environ.get("VERIPERF_TARGET_ROOT", DEFAULT_ROOT)` and insert at `sys.path[0]` so Phase 2 tests `ROOT` and Phase 4 tests `S/sandbox-opt/` with the exact same script.

## 2. Executing Benchmarks & Correctness Tests
- **Phase 2 Baseline:** `python "<VP>" bench-run --s "$S" --id opt1 --phase baseline -- python "$S/benches/bench_opt1.py"`
- **Phase 4 Dual Gate:** `python "<VP>" verify-opt --s "$S" --id opt1 --test-cmd "python -m pytest -p no:cacheprovider -q"` (or `python -m unittest discover -s tests`)

## 3. Harness Template (`S/benches/bench_opt1.py`)
```python
# ID: OPT-1 | Target: app/reconcile.py:match_orders
import gc, json, os, sys, time, tracemalloc
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[3]
TARGET_ROOT = Path(os.environ.get("VERIPERF_TARGET_ROOT", str(DEFAULT_ROOT)))
sys.path.insert(0, str(TARGET_ROOT))

# Optional: import canonical_hash from <skill_dir>/scripts/vp.py, or normalize floats/keys inline
import hashlib
def canonical_hash(val, digits=6):
    def norm(v):
        if isinstance(v, float): r = round(v, digits); return 0.0 if r == 0.0 else r
        if isinstance(v, dict): return {str(k): norm(val_k) for k, val_k in sorted(v.items())}
        if isinstance(v, (list, tuple)): return [norm(x) for x in v]
        return v
    return hashlib.sha256(json.dumps(norm(val), sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]

from app.reconcile import match_orders  # noqa: E402

WARMUP = int(os.environ.get("VERIPERF_WARMUP", "5"))
RUNS = int(os.environ.get("VERIPERF_RUNS", "20"))
TIERS = [("small", 200), ("medium", 2000), ("large", 10000)]

def make_data(n: int):
    orders = [{"id": f"ORD-{i}", "amt": (i % 97) + 1} for i in range(n)]
    fills = [{"order_id": f"ORD-{(i * 7) % n}", "qty": 1} for i in range(n)]
    return orders, fills

def bench_tier(tier: str, n: int) -> None:
    orders, fills = make_data(n)
    last_out = None
    for _ in range(WARMUP):
        last_out = match_orders(orders, fills)

    gc.collect()
    samples_ns = []
    for _ in range(RUNS):
        t0 = time.perf_counter_ns()
        last_out = match_orders(orders, fills)
        samples_ns.append(time.perf_counter_ns() - t0)

    # Measure peak Python heap separately so tracemalloc overhead does not skew timing
    gc.collect()
    tracemalloc.start()
    tracemalloc.reset_peak()
    _ = match_orders(orders, fills)
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    digest = canonical_hash(last_out)
    print("VERIPERF_METRIC:" + json.dumps({
        "tier": tier, "warmup": WARMUP, "runs": RUNS,
        "samples_ns": samples_ns, "peak_kb": round(peak_bytes / 1024.0, 2), "result_hash": digest,
    }))

if __name__ == "__main__":
    req_tier = os.environ.get("VERIPERF_TIER", "all")
    for t_name, n_items in TIERS:
        if req_tier in ("all", t_name):
            bench_tier(t_name, n_items)
```

## 4. Python Optimization Idioms & Anti-Illusion Traps
- **Avoid `tracemalloc` inside timed loops:** `tracemalloc` adds ~2–5x overhead to allocations; always measure memory in a separate single pass after timing.
- **Generator exhaustion trap:** Never return an unconsumed generator if measuring full computation; materialize only at the boundary or consume consistently in both baseline and opt.
- **Idiomatic wins:** `set`/`dict` lookup instead of `x in list`, `"".join(parts)` instead of `+=`, hoisted `re.compile`, `functools.lru_cache`, `collections.Counter`/`defaultdict`, `sqlite3` `WHERE id IN (...)` / `executemany`, streaming `for line in f:` instead of `.readlines()`.
