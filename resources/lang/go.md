# Go Performance Adapter

## 1. High-Resolution Clock & Memory APIs
- **Timer:** `time.Now()` / `time.Since(t0).Nanoseconds()` (monotonic clock built into Go `time.Time`).
- **Memory:** `runtime.GC()` + `var m1, m2 runtime.MemStats; runtime.ReadMemStats(&m1); ...; runtime.ReadMemStats(&m2)` -> `alloc_kb = float64(m2.TotalAlloc - m1.TotalAlloc) / 1024.0` (or `m2.HeapAlloc`).
- **Isolation:** Put temporary benchmark runner in `S/sandbox/` (copied via `vp.py sandbox --mode create`) for baseline, or `S/sandbox-opt/` for opt — never leave `*_test.go` or `main.go` files in host repo!

## 2. Executing Benchmarks & Correctness Tests
- **Phase 2 Baseline (in `S/sandbox/`):**
  `python "<VP>" bench-run --s "$S" --id opt1 --phase baseline --cwd "$S/sandbox" -- go test -count=1 -v -run TestVeriPerfOpt1 ./pkg/target/`
- **Phase 4 Dual Gate (in `S/sandbox-opt/`):**
  `python "<VP>" verify-opt --s "$S" --id opt1 --cwd "$S/sandbox-opt" --opt-bench-cmd "go test -count=1 -v -run TestVeriPerfOpt1 ./pkg/target/" --test-cmd "go test -count=1 ./..."`

## 3. Harness Template (`S/sandbox[-opt]/pkg/target/veriperf_opt1_test.go`)
```go
package target

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"runtime"
	"strconv"
	"testing"
	"time"
)

var veriPerfSink any

func envInt(k string, def int) int {
	if v, err := strconv.Atoi(os.Getenv(k)); err == nil && v > 0 { return v }
	return def
}

func TestVeriPerfOpt1(t *testing.T) {
	warmup, runs := envInt("VERIPERF_WARMUP", 5), envInt("VERIPERF_RUNS", 20)
	tiers := []struct { name string; n int }{{"small", 500}, {"medium", 5000}, {"large", 25000}}

	for _, tier := range tiers {
		input := buildInput(tier.n)
		var out any
		for i := 0; i < warmup; i++ { out = TargetFunc(input) }
		runtime.GC()
		samples := make([]int64, runs)
		for i := 0; i < runs; i++ {
			t0 := time.Now()
			out = TargetFunc(input)
			samples[i] = time.Since(t0).Nanoseconds()
		}
		veriPerfSink = out
		runtime.GC()
		var m1, m2 runtime.MemStats
		runtime.ReadMemStats(&m1)
		_ = TargetFunc(input)
		runtime.ReadMemStats(&m2)
		peakKB := float64(m2.TotalAlloc-m1.TotalAlloc) / 1024.0

		raw, _ := json.Marshal(out)
		sum := sha256.Sum256(raw)
		sJSON, _ := json.Marshal(samples)
		fmt.Printf("VERIPERF_METRIC:{\"tier\":%q,\"warmup\":%d,\"runs\":%d,\"samples_ns\":%s,\"peak_kb\":%.2f,\"result_hash\":%q}\n",
			tier.name, warmup, runs, string(sJSON), peakKB, hex.EncodeToString(sum[:8]))
	}
}
```

## 4. Go Optimization Idioms & Anti-Illusion Traps
- **Prevent compiler optimization:** Assign return values to a package-level `Sink` variable AND serialize into `result_hash`.
- **Disable Go test cache:** Always pass `-count=1` on `go test`.
- **Idiomatic wins:** Pre-size slices/maps (`make([]T, 0, n)`, `make(map[K]V, n)`), `strings.Builder` instead of `+`, hoist `regexp.MustCompile` to package vars, `map[K]struct{}` instead of linear slice scans, `bufio.Scanner` instead of `os.ReadFile`.
