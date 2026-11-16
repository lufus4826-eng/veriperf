# Node.js / TypeScript Performance Adapter

## 1. High-Resolution Clock & Memory APIs
- **Timer:** `process.hrtime.bigint()` (nanosecond `BigInt`; avoid `Date.now()`).
- **Memory:** Measure `process.memoryUsage().heapUsed` delta (or peak RSS via `process.memoryUsage().rss / 1024`) on a clean single pass with `global.gc?.()` (pass `--expose-gc` if available).
- **Modern Target Resolution (ESM, CommonJS & TS):** Resolve `const TARGET_ROOT = process.env.VERIPERF_TARGET_ROOT || DEFAULT_ROOT` and import dynamically via `pathToFileURL(path.join(TARGET_ROOT, 'src/...')).href` (`await import(...)`). This avoids `NODE_PATH` limitations with modern ESM packages (`"type": "module"`) and TypeScript runners (`tsx`), and guarantees Phase 2 runs `ROOT` while Phase 4 runs `S/sandbox-opt/`.

## 2. Executing Benchmarks & Correctness Tests
- **Phase 2 Baseline:** `python "<VP>" bench-run --s "$S" --id opt1 --phase baseline -- node "$S/benches/bench_opt1.mjs"` (for TS: `npx --no-install tsx "$S/benches/bench_opt1.mts"`)
- **Phase 4 Dual Gate:** `python "<VP>" verify-opt --s "$S" --id opt1 --auto-rollback --test-cmd "node --test"` (or `npx --no-install jest --ci --coverage=false` / `vitest run`)

## 3. Harness Template (`S/benches/bench_opt1.mjs`)
```javascript
// ID: OPT-1 | Target: src/reconcile.js:matchOrders
import crypto from 'node:crypto';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = path.resolve(__dirname, '../../..');
const TARGET_ROOT = process.env.VERIPERF_TARGET_ROOT || DEFAULT_ROOT;
const mod = await import(pathToFileURL(path.join(TARGET_ROOT, 'src/reconcile.js')).href);
const matchOrders = mod.matchOrders || mod.default?.matchOrders;

function canonicalize(v, digits = 6) {
  if (typeof v === 'number') {
    if (!Number.isFinite(v)) return String(v);
    const r = Number(v.toFixed(digits));
    return r === 0 ? 0 : r;
  }
  if (Array.isArray(v)) return v.map((x) => canonicalize(x, digits));
  if (v && typeof v === 'object') {
    return Object.fromEntries(Object.keys(v).sort().map((k) => [k, canonicalize(v[k], digits)]));
  }
  return v;
}

const WARMUP = Number(process.env.VERIPERF_WARMUP || 5);
const RUNS = Number(process.env.VERIPERF_RUNS || 20);
const REQ_TIER = process.env.VERIPERF_TIER || 'all';
const TIERS = [['small', 200], ['medium', 2000], ['large', 10000]];

for (const [tier, n] of TIERS) {
  if (REQ_TIER !== 'all' && REQ_TIER !== tier) continue;
  const input = Array.from({ length: n }, (_, i) => ({ id: `ID-${i}`, val: i % 97 }));
  let out;
  for (let i = 0; i < WARMUP; i++) out = matchOrders(input);

  if (global.gc) global.gc();
  const samplesNs = [];
  for (let i = 0; i < RUNS; i++) {
    const t0 = process.hrtime.bigint();
    out = matchOrders(input);
    samplesNs.push(Number(process.hrtime.bigint() - t0));
  }

  const memBefore = process.memoryUsage().heapUsed;
  const _m = matchOrders(input);
  const peakKB = Math.max(0, (process.memoryUsage().heapUsed - memBefore) / 1024);
  const hash = crypto.createHash('sha256').update(JSON.stringify(canonicalize(out))).digest('hex').slice(0, 16);
  console.log('VERIPERF_METRIC:' + JSON.stringify({
    tier, warmup: WARMUP, runs: RUNS, samples_ns: samplesNs, peak_kb: Number(peakKB.toFixed(2)), result_hash: hash,
  }));
}
```

## 4. JS/TS Optimization Idioms & Anti-Illusion Traps
- **V8 JIT warm-up:** Always run `>= 5` warm-up iterations so V8 TurboFan optimizes hot functions before timing.
- **Avoid polymorphic hidden classes:** Keep object shapes consistent; use `Map`/`Set` instead of `arr.includes()`/`arr.find()` inside loops.
- **Idiomatic wins:** `Set`/`Map` O(1) lookup, `readline` / `fs.createReadStream` instead of `fs.readFileSync` on huge files, hoist `new RegExp(...)`, `Promise.all` with bounded concurrency instead of serial `await` in loops, avoid `JSON.parse(JSON.stringify(...))` deep clones in loops.
