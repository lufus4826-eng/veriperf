"""Audit log aggregation module (regex + string += hotspot for OPT-2)."""

from __future__ import annotations

import json
import re
from typing import Dict, List


def aggregate_audit_logs(raw_lines: List[str]) -> Dict[str, object]:
    """Parse JSON audit records, extract valid ticker tags, and build summary metrics."""
    summary_text = ""
    totals: Dict[str, int] = {}
    for raw in raw_lines:
        rec = json.loads(raw)
        if not rec.get("ok"):
            continue
        m = re.match(r"^([A-Z]{2,6})-(\d{4})$", rec["code"])
        if not m:
            continue
        sym = m.group(1)
        amt = int(rec["amount"])
        totals[sym] = totals.get(sym, 0) + amt
        line = f"{sym}:{amt}\n"
        summary_text += line
    return {"totals": totals, "digest_len": len(summary_text), "lines": summary_text.count("\n")}
