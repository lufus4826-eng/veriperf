# VeriPerf Benchmark Report Template (English — Lazy-Load in Phase 5 Only)

> **Instructions:** Copy below the divider to `S/perf-report.md`. Complete all 6 sections; write `"None"` if empty. **NEVER delete sections.** Use exact measured values from `vp.py summary` and unified diffs from `vp.py sandbox --mode diff`.

---

# VeriPerf Statistical Optimization Report

## Session Metadata
- **Session ID (`S`):** `.bughunt/<SNAME>/`
- **Project Root (`ROOT`):** `<path>`
- **Scope:** <Entire project OR module subpath>
- **Measurement Protocol:** Warm-up `<W>` runs | Timed `<R>` runs | Minimum Speedup Threshold `>= 15.0%` (`p50`) & `p95` non-regression | Correctness Gate: Project test suite + output `result_hash` match
- **Report Status:** Final Complete

---

## 1. Executive Summary & Aggregate Metrics

| Metric | Value | Notes |
|---|---:|---|
| Static Hotspots Scanned | <N> | Via `vp.py hotspot-scan` |
| Candidates Benchmarked | <N> | `OPT-1 .. OPT-N` |
| **ACCEPTED Optimizations (`>=15%` + Dual Gate)** | **<N>** | Retained in `S/sandbox-opt` diff |
| **REJECTED / Filtered Candidates** | **<N>** | `<15%` gain or Gate 1 failure (reverted) |
| Peak Speedup (`p50`, Primary Tier) | <+X.X% (Xx)> | `<ID>` |
| Peak Memory Reduction | <+X.X%> | `<ID>` |

---

## 2. Workload & Environment Profile
- **Runtime / Hardware Context:** <Language & version, OS, CPU/execution notes>
- **Baseline Test Suite:** `<command>` -> `<status>`
- **Workload Tiers:**
  - **Small:** <Exact N / payload size>
  - **Medium:** <Exact N / payload size>
  - **Large (Primary Gate Tier):** <Exact N / payload size>

---

## 3. ACCEPTED Optimizations (`>=15%` Speedup & Dual-Gate Verified)

<!-- Repeat per ACCEPTED optimization -->

### <ID>: <Short Optimization Title>
- **Code Location:** [`<relative/path:line>`](<relative/path>) `<symbol>`
- **Bottleneck Root Cause:** <Why it was slow (e.g., O(N^2) nested list lookup / repeated regex compile)>
- **Optimization Mechanism:** <What changed (e.g., indexed dict lookup / pre-compiled regex + join)>
- **Dual Gate Verdict:** **ACCEPTED** (`Gate 1: PASS — tests PASS & hashes MATCH` | `Gate 2: PASS — +X.X% >= 15.0%`)
- **Benchmark Harness:** [`<S>/benches/<file>`](<S>/benches/<file>)

#### Before vs. After Statistical Comparison
| Tier | Baseline p50 | Opt p50 | Speedup (p50) | Baseline p95 | Opt p95 | Baseline Peak Mem | Opt Peak Mem | Mem Delta | Output Hash |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| small | <X> ms | <X> ms | <+X%> (<X>x) | <X> ms | <X> ms | <X> KB | <X> KB | <X%> | `<hash>` (MATCH) |
| medium | <X> ms | <X> ms | <+X%> (<X>x) | <X> ms | <X> ms | <X> KB | <X> KB | <X%> | `<hash>` (MATCH) |
| **large*** | <X> ms | <X> ms | **<+X%> (<X>x)** | <X> ms | <X> ms | <X> KB | <X> KB | <X%> | `<hash>` (MATCH) |

#### Sandbox-Verified Unified Diff
```diff
<paste from vp.py sandbox --mode diff>
```

---

## 4. REJECTED / Filtered Candidates (`<15%` Gain or Gate 1 Failure)

> VeriPerf automatically rejects and reverts any optimization gaining `<15.0%` on the primary tier, causing `p95` regression, failing project tests, or altering output hashes.

| ID | Location | Attempted Change | Primary Tier | Base p50 | Opt p50 | Speedup % | Rejection Reason | Action Taken |
|---|---|---|---|---:|---:|---:|---|---|
| <ID OR None> | <path:line> | <summary> | <large> | <X> ms | <X> ms | <+X.X%> | <REJECTED_INSUFFICIENT_GAIN / ...> | Reverted from sandbox-opt |

---

## 5. Workspace Cleanliness Log
- **Snapshot Files:** `<S>/logs/fs-before.txt`, `<S>/logs/fs-after.txt`
- **Cleanliness Verdict:** <cleanliness: CLEAN (0 unexpected host changes) | details>
- **Deliverables Retained:** `<S>/perf-report.md`, `<S>/worklog.md`, `<S>/benches/`, `<S>/logs/`

---

## 6. Applied Optimizations Log
> Updated only after explicit user approval to patch host files.

| Timestamp | ID | Patched File | Host Test Suite | Host Confirmation `p50` | Status |
|---|---|---|---|---|---|
| None | - | - | - | - | Pending User Approval |
