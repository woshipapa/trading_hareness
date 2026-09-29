"""Pure validation for scheduled provider canary probes.

The scheduler/health writer is injected by the owner runtime.  This module
only classifies the response, keeping rejected, empty, stale and malformed
payloads distinct for dashboards and fallbacks.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


def classify_canary_failure(error: Any) -> str:
    text = str(error or "").lower()
    if any(marker in text for marker in ("timeout", "timed out", "deadline")):
        return "timeout"
    if any(marker in text for marker in ("permission", "not purchased", "unauthorized", "forbidden", "rejected")):
        return "rejected"
    if any(marker in text for marker in ("empty", "no data", "no rows")):
        return "empty"
    return "unreachable"


def validate_quote_canary(
    rows: Sequence[Mapping[str, Any]], *, observed_at: datetime,
    timestamp_keys: tuple[str, ...] = ("exchange_time", "upstream_observed_at", "timestamp"),
    max_age_seconds: float = 90.0,
) -> dict[str, Any]:
    """Validate quote shape, price relationships and timestamp freshness."""
    if not rows:
        return {"status": "empty", "row_count": 0, "reasons": ["no_rows"], "decision_eligible": False}
    reasons: list[str] = []
    timestamps: list[datetime] = []
    for index, row in enumerate(rows):
        try:
            opened = float(row.get("open"))
            high = float(row.get("high"))
            low = float(row.get("low"))
            close = float(row.get("close"))
        except (TypeError, ValueError):
            reasons.append(f"row_{index}:invalid_ohlc")
            continue
        if min(opened, high, low, close) <= 0:
            reasons.append(f"row_{index}:non_positive_price")
        if high < max(opened, close) or low > min(opened, close):
            reasons.append(f"row_{index}:ohlc_relationship")
        timestamp = next((row.get(key) for key in timestamp_keys if row.get(key) not in (None, "")), None)
        if isinstance(timestamp, datetime):
            parsed = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
        else:
            try:
                parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                parsed = None
        if parsed is None:
            reasons.append(f"row_{index}:missing_timestamp")
        else:
            timestamps.append(parsed.astimezone(timezone.utc))
    if timestamps:
        age = (observed_at.astimezone(timezone.utc) - max(timestamps)).total_seconds()
        if age < -5:
            reasons.append("future_timestamp")
        elif age > max_age_seconds:
            reasons.append("stale_timestamp")
    status = "invalid" if reasons else "healthy"
    return {
        "status": status, "row_count": len(rows), "reasons": reasons,
        "decision_eligible": status == "healthy", "max_age_seconds": max_age_seconds,
    }


__all__ = ["classify_canary_failure", "validate_quote_canary"]
