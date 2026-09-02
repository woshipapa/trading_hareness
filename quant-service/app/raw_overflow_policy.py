"""Bounded admission policy for raw evidence overflow archiving.

This module is deliberately side-effect free.  It decides whether a raw batch
may stay hot, should be handed to the asynchronous cold writer, or must be
reduced to strategy-critical evidence.  It never uploads, deletes, or changes
live signal eligibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


OverflowState = Literal["normal", "cloud_overflow", "critical"]


@dataclass(frozen=True)
class RawOverflowLimits:
    warning_ratio: float = 0.80
    stop_ratio: float = 0.90
    max_batch_bytes: int = 256 * 1024 * 1024
    max_spool_bytes: int = 256 * 1024 * 1024
    max_queue_batches: int = 1_000


def classify(
    *,
    hot_used_bytes: int,
    hot_budget_bytes: int,
    queue_batches: int,
    spool_bytes: int,
    cloud_enabled: bool,
    limits: RawOverflowLimits = RawOverflowLimits(),
) -> tuple[OverflowState, tuple[str, ...]]:
    """Classify raw admission without ever allowing an unbounded path."""
    budget = max(1, int(hot_budget_bytes))
    ratio = max(0, int(hot_used_bytes)) / budget
    queue_full = int(queue_batches) >= limits.max_queue_batches
    spool_full = int(spool_bytes) >= limits.max_spool_bytes
    reasons: list[str] = []
    if ratio >= limits.stop_ratio:
        reasons.append("hot_database_stop_watermark")
    elif ratio >= limits.warning_ratio:
        reasons.append("hot_database_warning_watermark")
    if queue_full:
        reasons.append("archive_queue_full")
    if spool_full:
        reasons.append("archive_spool_full")
    if ratio >= limits.stop_ratio or queue_full or spool_full:
        return "critical", tuple(reasons)
    if ratio >= limits.warning_ratio and cloud_enabled:
        return "cloud_overflow", tuple(reasons)
    return "normal", tuple(reasons)


def batch_allowed(*, batch_bytes: int, limits: RawOverflowLimits = RawOverflowLimits()) -> bool:
    """Reject oversized batches before they can enter memory or the queue."""
    return 0 < int(batch_bytes) <= limits.max_batch_bytes


__all__ = ["OverflowState", "RawOverflowLimits", "batch_allowed", "classify"]
