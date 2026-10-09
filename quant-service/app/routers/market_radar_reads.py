"""Read-only market radar route: a day's minute points and the market's main net flow."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter

from ..market_radar import CN_TZ
from ..market_radar_runtime import radar_day


def build_market_radar_router(database: Any, run_database_blocking: Callable[..., Awaitable[Any]]) -> APIRouter:
    router = APIRouter(tags=["market-radar"])

    @router.get("/api/v1/market/radar")
    async def market_radar(trade_date: date | None = None, include_entered: bool = False) -> dict[str, Any]:
        day = trade_date or datetime.now(CN_TZ).date()

        def read() -> dict[str, Any]:
            with database.transaction() as connection:
                return radar_day(connection, day, include_entered=include_entered)

        return await run_database_blocking(read, timeout_seconds=30)

    return router


__all__ = ["build_market_radar_router"]
