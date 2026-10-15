"""Pure helpers for bounded intraday scan performance evidence.

The timing payload is persisted as research telemetry beside a scan.  It has no
effect on signal eligibility, alert state, or provider selection.  Keeping the
shape in a small pure module lets the runtime and tests share one contract
without importing the application composition root.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


SCAN_TIMING_SCHEMA_VERSION = "intraday-scan-timing-v1"
SCAN_TIMING_STAGES = (
    "session_and_watchlist",
    "pre_capture",
    "market_wide_research",
    "capture_and_memberships",
    "enrichment_and_strategy_prepare",
    "signal_persist",
    "delivery_and_research",
    "shadow_rotation",
)


def _milliseconds(value: Any) -> float:
    """Normalize one monotonic duration into a small JSON-safe millisecond value."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, numeric), 1)


def build_scan_performance(
    *,
    status: str,
    stage_ms: Mapping[str, Any],
    total_ms: Any,
    persistence_status: str = "pending",
) -> dict[str, Any]:
    """Build the versioned, research-only timing projection for one scan.

    Only declared stages are emitted, in a stable order.  This prevents an
    accidentally supplied internal label from becoming an API contract and
    keeps the dashboard deterministic as new stages are added deliberately.
    """
    return {
        "schema_version": SCAN_TIMING_SCHEMA_VERSION,
        "research_only": True,
        "live_effect": "none",
        "status": str(status or "unknown"),
        "total_ms": _milliseconds(total_ms),
        "persistence_status": str(persistence_status or "pending"),
        "stage_ms": {
            stage: _milliseconds(stage_ms.get(stage))
            for stage in SCAN_TIMING_STAGES
            if stage in stage_ms
        },
        "overlap": {
            "capture_and_memberships": "concurrent",
            "capture_and_board_cache": "concurrent_after_watchlist",
            "enrichment_and_board_cache": "concurrent",
            "leader_flow_and_primary_persist": "concurrent_after_capture",
            "alert_delivery_and_leader_flow": "concurrent_after_receipt",
            "shadow_rotation_and_delivery": "concurrent_after_receipt",
        },
    }


__all__ = ["SCAN_TIMING_SCHEMA_VERSION", "SCAN_TIMING_STAGES", "build_scan_performance"]
