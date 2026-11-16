# Hotspot Patterns & Remediation Catalog (Phase 0–1)

Inspect `vp.py hotspot-scan` hits against real call paths before selecting targets. Prioritize bottlenecks on hot execution paths where `large` tier workload scales super-linearly or causes excess I/O / RAM spikes.

| Category | Pattern Examples | Typical Complexity / Failure | Proven Remediation |
|---|---|---|---|
| **1. `NESTED_LOOP`** | Nested `for`/`while`, `x in list_var` inside loop, `.index()`/`.remove()` in loop, `.includes()`/`.find()` in JS/Go/Rust loop | `O(N * M)` or `O(N^2)` CPU blowup as data grows from `medium` to `large` | Pre-index right-hand collection into `dict`/`set`/`HashMap`/`Map` (`O(N + M)`), or sort + two-pointer / binary search |
| **2. `N_PLUS_ONE_IO`** | SQL/ORM `query`/`execute`/`find` inside loop, `requests.get`/`fetch`/`http.Get` per item, `open()` inside loop | Latency = `N * RTT` + connection/FD exhaustion | Bulk `WHERE id IN (...)` batch query, eager join, `executemany`, connection reuse, or bounded async concurrency |
| **3. `UNBOUNDED_MEMORY_READ`** | `.read()`, `.readlines()`, `.read_text()`, `fs.readFileSync`, `os.ReadFile`, `Files.readAllBytes`, `fetchall()` without pagination | Peak RSS = multiple × file size; GC stalls or OOM under `large` input | Stream line-by-line / chunked (`for line in f`, `bufio.Scanner`, `BufReader`, `pd.read_csv(chunksize=...)`, cursor batching) |
| **4. `LOOP_ALLOC_OR_RECOMPILE`** | String `s += ...` in loop, `re.compile`/`new RegExp`/`Pattern.compile`/`Regex::new` inside loop, `json.loads`/`deepcopy`/`sorted()` in loop | Quadratic string copying (`O(N^2)` in naive allocators) or repeated parser/regex setup cost | Hoist compiled regex outside loop, accumulate in list/builder (`"".join`, `strings.Builder`, `StringBuilder`), hoist invariants, use `@lru_cache` for pure hot calls |
| **5. `BLOCKING_OR_CONTENTION`** | Synchronous sleep/I/O inside async event loop, coarse lock held across slow I/O, repeated redundant recomputation | Event-loop starvation, thread pool queuing, high `p95` tail spikes | Offload blocking calls, narrow lock scope (compute outside lock), memoize pure lookups with bounded cache |
