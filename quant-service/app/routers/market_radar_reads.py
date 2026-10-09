"""Read-only market-wide routes: the minute radar, and the session's limit-up detail."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter

from ..market_radar import CN_TZ
from ..limit_detail_read_model import limit_detail_day
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

    @router.get("/api/v1/market/limit-detail")
    async def market_limit_detail(trade_date: date | None = None) -> dict[str, Any]:
        day = trade_date or datetime.now(CN_TZ).date()

        def read() -> dict[str, Any]:
            with database.transaction() as connection:
                return limit_detail_day(connection, day)

        return await run_database_blocking(read, timeout_seconds=30)

    return router


__all__ = ["build_market_radar_router"]
