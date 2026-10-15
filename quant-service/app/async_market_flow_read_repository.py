"""Native-async projection for stored multiscale market-flow evidence."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .market_flow_read_model import MARKET_FLOW_SNAPSHOT_SQL, project_market_flow_snapshot


async def market_flow_features(async_database: Any, trade_date: date | None = None, *, limit: int = 720) -> dict[str, Any]:
    china = ZoneInfo("Asia/Shanghai")
    selected_date = trade_date or datetime.now(timezone.utc).astimezone(china).date()
    bounded_limit = max(1, min(int(limit), 1000))
    async with async_database.transaction() as connection:
        result = await connection.execute(
            MARKET_FLOW_SNAPSHOT_SQL,
            (selected_date, bounded_limit, selected_date, selected_date),
        )
        row = await result.fetchone()
    return project_market_flow_snapshot(row, selected_date)


__all__ = ["market_flow_features"]
