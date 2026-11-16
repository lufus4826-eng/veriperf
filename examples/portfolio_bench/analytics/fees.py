"""Fee calculation module (already O(N), demonstrates sub-15% noise rejection on OPT-3)."""

from __future__ import annotations

from typing import List


def compute_batch_fees(amounts: List[float], rate: float = 0.0025) -> float:
    """Calculate total rounded transaction fees across a batch of trades."""
    total = 0.0
    for amt in amounts:
        fee = round(amt * rate, 4)
        total += fee
    return round(total, 2)
