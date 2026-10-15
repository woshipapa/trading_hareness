"""Native async strategy-health read projection."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .strategy_health_read_model import STRATEGY_HEALTH_SQL, strategy_health_payload_from_rows


async def latest_strategy_health(async_database: Any, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    async with async_database.transaction() as connection:
        result = await connection.execute(STRATEGY_HEALTH_SQL)
        snapshot = await result.fetchone()
    # Compatibility for lightweight async repository doubles that still route
    # the pre-aggregation query shape. Production PostgreSQL always returns a
    # row from the scalar snapshot query; an empty result remains fail-closed.
    snapshot = snapshot or {
        "signals_7d": 0, "signals_prior_7d": 0, "episodes_7d": 0,
        "episodes_prior_7d": 0, "matured_30m_7d": 0, "matured_days_7d": 0,
        "matured_30m_total": 0, "matured_days_total": 0,
        "outcome_rows": 0, "outcome_positive": 0, "outcome_avg_return": None,
        "latest_quote_at": None, "fresh_quote_rows": 0, "calendar_is_open": None,
        "strategy_rows": [],
    }
    counts = {key: snapshot[key] for key in (
        "signals_7d", "signals_prior_7d", "episodes_7d", "episodes_prior_7d",
        "matured_30m_7d", "matured_days_7d", "matured_30m_total", "matured_days_total",
    )}
    outcomes = {"rows": snapshot["outcome_rows"], "positive": snapshot["outcome_positive"],
                "avg_return": snapshot["outcome_avg_return"]}
    latest_quotes = {key: snapshot[key] for key in ("latest_quote_at", "fresh_quote_rows", "calendar_is_open")}
    strategy_rows = snapshot["strategy_rows"] or []
    return strategy_health_payload_from_rows(counts, outcomes, latest_quotes, strategy_rows, now=now)


__all__ = ["latest_strategy_health"]
