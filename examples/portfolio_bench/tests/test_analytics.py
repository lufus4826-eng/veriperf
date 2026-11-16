"""Correctness unit tests for portfolio analytics demo."""

import json
import unittest

from analytics import aggregate_audit_logs, compute_batch_fees, reconcile_trades


class TestAnalytics(unittest.TestCase):
    def test_reconcile_trades(self) -> None:
        orders = [
            {"order_id": "O1", "qty": 10},
            {"order_id": "O2", "qty": 5},
        ]
        settlements = [
            {"order_id": "O1", "qty": 4, "price": 10.0},
            {"order_id": "O1", "qty": 6, "price": 10.5},
            {"order_id": "O2", "qty": 3, "price": 20.0},
        ]
        res = reconcile_trades(orders, settlements)
        self.assertEqual(res[0], {"order_id": "O1", "status": "MATCHED", "filled_qty": 10, "notional": 103.0})
        self.assertEqual(res[1], {"order_id": "O2", "status": "MISMATCH", "filled_qty": 3, "notional": 60.0})

    def test_aggregate_audit_logs(self) -> None:
        lines = [
            json.dumps({"ok": True, "code": "NVDA-1001", "amount": 50}),
            json.dumps({"ok": False, "code": "NVDA-1002", "amount": 99}),
            json.dumps({"ok": True, "code": "bad", "amount": 10}),
            json.dumps({"ok": True, "code": "MSFT-2002", "amount": 25}),
            json.dumps({"ok": True, "code": "NVDA-1003", "amount": 15}),
        ]
        out = aggregate_audit_logs(lines)
        self.assertEqual(out["totals"], {"NVDA": 65, "MSFT": 25})
        self.assertEqual(out["lines"], 3)
        self.assertEqual(out["digest_len"], len("NVDA:50\nMSFT:25\nNVDA:15\n"))

    def test_compute_batch_fees(self) -> None:
        self.assertEqual(compute_batch_fees([100.0, 250.5, 400.0], 0.01), 7.5)


if __name__ == "__main__":
    unittest.main()
