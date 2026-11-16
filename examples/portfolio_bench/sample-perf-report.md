# VeriPerf Statistical Optimization Report (Sample Showcase)

> Generated from [`examples/portfolio_bench/run_demo.py`](run_demo.py) using **VeriPerf v1.1.0**.

## Session Metadata
- **Session ID (`S`):** `.bughunt/perf-20261116-205732/`
- **Project Root (`ROOT`):** `examples/portfolio_bench`
- **Scope:** Entire package (`analytics/`)
- **Measurement Protocol:** Warm-up `5` runs | Timed `20` runs | Minimum Speedup `>= 15.0%` (`p50`) | `p95` non-regression | Max Peak Memory Growth `<= +25.0%` | Counterbalanced `ABBA` Interleaved Re-Verification (`--interleave auto`) | Canonical Hash + Project Test Suite
- **Report Status:** Final Complete

---

## 1. Executive Summary & Aggregate Metrics

| Metric | Value | Notes |
|---|---:|---|
| Static Hotspots Scanned | 4 | Via `vp.py hotspot-scan` |
| Candidates Benchmarked | 3 | `OPT-1 .. OPT-3` |
| **ACCEPTED Optimizations (`>=15%` + Dual Gate)** | **2** | Retained in `S/sandbox-opt` diff (`opt1`, `opt2`) |
| **REJECTED / Filtered Candidates** | **1** | `opt3` (`+3.1% < 15.0%`, auto-rolled back) *(plus `opt2` Attempt 1 rejected & rolled back)* |
| Peak Speedup (`p50`, Primary Tier) | +98.8% (80.25x) | `opt1` (`144.637 ms` -> `1.802 ms`) |
| Peak Memory Reduction | +95.5% | `opt2` (`70.2 KB` -> `3.1 KB`) |

---

## 2. Workload & Environment Profile
- **Runtime / Hardware Context:** Python 3.13 on Windows (`win32`)
- **Baseline Test Suite:** `python -m unittest discover -s tests -q` -> `OK`
- **Workload Tiers:**
  - **Small:** `opt1` (100 orders / 200 settlements) | `opt2` (200 JSON lines) | `opt3` (2,000 amounts)
  - **Medium:** `opt1` (500 orders / 1,000 settlements) | `opt2` (2,000 JSON lines) | `opt3` (20,000 amounts)
  - **Large (Primary Gate Tier):** `opt1` (1,500 orders / 3,000 settlements) | `opt2` (10,000 JSON lines) | `opt3` (100,000 amounts)

---

## 3. ACCEPTED Optimizations (`>=15%` Speedup & Dual-Gate Verified)

### OPT-1: Indexed In-Place Hash Lookup (`O(N*M)` -> `O(N + M)`)
- **Code Location:** [`analytics/reconcile.py:8`](analytics/reconcile.py) `reconcile_trades`
- **Bottleneck Root Cause:** Inner loop linearly scanned `settlements` for each `order`, causing `O(N * M)` comparisons.
- **Optimization Mechanism:** Pre-built output entries keyed in `by_order[order_id]` so `settlements` update matched entries directly in `O(1)` without intermediate list allocations (keeping memory growth at `+16.0% <= +25.0%`).
- **Dual Gate Verdict:** **ACCEPTED** (`Gate 1: PASS — tests PASS (135ms) & hashes MATCH` | `Gate 2: PASS — +98.8% >= 15.0%, p95=+98.7%, mem=+16.0%<=+25%`)

#### Before vs. After Statistical Comparison
| Tier | Baseline p50 | Opt p50 | Speedup (p50) | Baseline p95 | Opt p95 | Baseline Peak Mem | Opt Peak Mem | Mem Delta | Output Hash |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| small | 0.793 ms | 0.115 ms | +85.5% (6.89x) | 1.004 ms | 0.118 ms | 21.3 KB | 24.6 KB | -15.6% | `500dd536` (MATCH) |
| medium | 17.461 ms | 0.591 ms | +96.6% (29.53x) | 18.380 ms | 0.597 ms | 105.8 KB | 118.6 KB | -12.1% | `e54c305c` (MATCH) |
| **large*** | 144.637 ms | 1.802 ms | **+98.8% (80.25x)** | 146.383 ms | 1.857 ms | 317.2 KB | 368.0 KB | -16.0% | `b656c995` (MATCH) |

#### Sandbox-Verified Unified Diff
```diff
--- a/analytics/reconcile.py
+++ b/analytics/reconcile.py
@@ -1,28 +1,22 @@
-"""Trade reconciliation module (contains O(N^2) hotspot for OPT-1)."""
-
+"""Trade reconciliation module (optimized O(N + M) in-place dict index)."""
 from __future__ import annotations
-
 from typing import Dict, List
 
-
 def reconcile_trades(orders: List[Dict], settlements: List[Dict]) -> List[Dict]:
-    """Match each order with settlements using O(N*M) linear scan (baseline)."""
     results: List[Dict] = []
+    by_order: Dict[str, Dict] = {}
     for order in orders:
-        oid = order["order_id"]
-        matched_qty = 0
-        matched_notional = 0.0
-        for s in settlements:
-            if s["order_id"] == oid:
-                matched_qty += s["qty"]
-                matched_notional += s["qty"] * s["price"]
-        status = "MATCHED" if matched_qty == order["qty"] else "MISMATCH"
-        results.append(
-            {
-                "order_id": oid,
-                "status": status,
-                "filled_qty": matched_qty,
-                "notional": round(matched_notional, 2),
-            }
-        )
+        entry = {"order_id": order["order_id"], "status": "MISMATCH", "filled_qty": 0, "notional": 0.0, "_req": order["qty"]}
+        results.append(entry)
+        by_order[order["order_id"]] = entry
+    for s in settlements:
+        entry = by_order.get(s["order_id"])
+        if entry is not None:
+            q = s["qty"]
+            entry["filled_qty"] += q
+            entry["notional"] += q * s["price"]
+    for entry in results:
+        req = entry.pop("_req")
+        entry["status"] = "MATCHED" if entry["filled_qty"] == req else "MISMATCH"
+        entry["notional"] = round(entry["notional"], 2)
     return results
```

---

### OPT-2: Zero-Buffer Streaming & O(1) ASCII Validation (`audit_agg.py`)
- **Code Location:** [`analytics/audit_agg.py:9`](analytics/audit_agg.py) `aggregate_audit_logs`
- **Bottleneck Root Cause:** Built and concatenated an entire `summary_text` string inside the loop just to compute `len()` and `.count("\n")`, while compiling/matching a regex on every record.
- **Optimization Mechanism:** Replaced `re.match` with O(1) ASCII slice validation and tracked `digest_len` and `valid_lines` directly in integers. Because `p50` gain was near the 15% border (`+16.1%`), VeriPerf automatically triggered counterbalanced `ABBA` interleaved verification before accepting.
- **Dual Gate Verdict:** **ACCEPTED** (`Gate 1: PASS — tests PASS (111ms) & hashes MATCH` | `Gate 2: PASS — +16.6% >= 15.0%, p95=+6.3%, mem=-95.5%<=+25%` | `interleaved=YES`)

#### Before vs. After Statistical Comparison
| Tier | Baseline p50 | Opt p50 | Speedup (p50) | Baseline p95 | Opt p95 | Baseline Peak Mem | Opt Peak Mem | Mem Delta | Output Hash |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| small | 0.516 ms | 0.430 ms | +16.7% (1.20x) | 0.542 ms | 0.434 ms | 4.6 KB | 3.0 KB | +33.5% | `63d7e0e6` (MATCH) |
| medium | 5.072 ms | 4.202 ms | +17.2% (1.21x) | 5.209 ms | 4.277 ms | 16.7 KB | 3.1 KB | +81.2% | `00d2ca8e` (MATCH) |
| **large*** | 25.601 ms | 21.339 ms | **+16.6% (1.20x)** | 26.100 ms | 24.443 ms | 70.2 KB | 3.1 KB | **+95.5%** | `8d9a5492` (MATCH) |

#### Sandbox-Verified Unified Diff
```diff
--- a/analytics/audit_agg.py
+++ b/analytics/audit_agg.py
@@ -1,26 +1,23 @@
-"""Audit log aggregation module (regex + string += hotspot for OPT-2)."""
-
+"""Audit log aggregation (Attempt 2: zero-buffer streaming + O(1) ASCII slice check)."""
 from __future__ import annotations
-
 import json
-import re
 from typing import Dict, List
 
-
 def aggregate_audit_logs(raw_lines: List[str]) -> Dict[str, object]:
-    """Parse JSON audit records, extract valid ticker tags, and build summary metrics."""
-    summary_text = ""
     totals: Dict[str, int] = {}
+    loads = json.loads
+    digest_len = 0
+    valid_lines = 0
     for raw in raw_lines:
-        rec = json.loads(raw)
-        if not rec.get("ok"):
-            continue
-        m = re.match(r"^([A-Z]{2,6})-(\d{4})$", rec["code"])
-        if not m:
-            continue
-        sym = m.group(1)
+        rec = loads(raw)
+        if not rec.get("ok"): continue
+        code = rec["code"]
+        n = len(code)
+        if n < 7 or n > 11 or code[n - 5] != "-": continue
+        sym, num = code[: n - 5], code[n - 4 :]
+        if not (sym.isascii() and sym.isalpha() and sym.isupper() and num.isascii() and num.isdigit()): continue
         amt = int(rec["amount"])
         totals[sym] = totals.get(sym, 0) + amt
-        line = f"{sym}:{amt}\n"
-        summary_text += line
-    return {"totals": totals, "digest_len": len(summary_text), "lines": summary_text.count("\n")}
+        digest_len += (n - 5) + len(str(amt)) + 2
+        valid_lines += 1
+    return {"totals": totals, "digest_len": digest_len, "lines": valid_lines}
```

---

## 4. REJECTED / Filtered Candidates (`<15%` Gain, `p95` Regression, or Memory Blowup)

| ID | Location | Attempted Change | Primary Tier | Base p50 | Opt p50 | Speedup % | Rejection Reason | Action Taken |
|---|---|---|---|---:|---:|---:|---|---|
| OPT-2 *(Attempt 1)* | `analytics/audit_agg.py:9` | Hoisted regex + buffered strings in `list.append` (`+537.8%` RAM) | large *(interleaved)* | 25.41 ms | 21.17 ms | +16.7% | `REJECTED_P95_REGRESSION` (`-33.4%`) | `AUTO_RESTORED` (`checkpoint:opt2`) |
| OPT-3 | `analytics/fees.py:8` | Local variable binding (`rnd = round`) | large | 41.414 ms | 40.139 ms | +3.1% | `REJECTED_INSUFFICIENT_GAIN` (`<15.0%`) | `AUTO_RESTORED` (`checkpoint:opt3`) |

---

## 5. Workspace Cleanliness Log
- **Cleanliness Verdict:** `cleanliness: CLEAN (0 new/modified files outside .bughunt/)`

---

## 6. Applied Optimizations Log
| Timestamp | ID | Patched File | Host Test Suite | Host Confirmation `p50` | Status |
|---|---|---|---|---|---|
| None | - | - | - | - | Pending User Approval |
