"""Trade reconciliation module (contains O(N^2) hotspot for OPT-1)."""

from __future__ import annotations

from typing import Dict, List


def reconcile_trades(orders: List[Dict], settlements: List[Dict]) -> List[Dict]:
    """Match each order with settlements using O(N*M) linear scan (baseline)."""
    results: List[Dict] = []
    for order in orders:
        oid = order["order_id"]
        matched_qty = 0
        matched_notional = 0.0
        for s in settlements:
            if s["order_id"] == oid:
                matched_qty += s["qty"]
                matched_notional += s["qty"] * s["price"]
        status = "MATCHED" if matched_qty == order["qty"] else "MISMATCH"
        results.append(
            {
                "order_id": oid,
                "status": status,
                "filled_qty": matched_qty,
                "notional": round(matched_notional, 2),
            }
        )
    return results
