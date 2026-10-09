"""Read-only session routes: the minute radar, the limit-up detail, and the strategy cards."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Query

from ..market_radar import CN_TZ
from ..limit_detail_read_model import limit_detail_day
from ..market_radar_runtime import radar_day
from ..strategy_cards_read_model import strategy_cards


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

    @router.get("/api/v1/strategies/cards")
    async def strategy_cards_route(trade_date: date | None = None,
                                   per_line: int = Query(default=10, ge=1, le=30)) -> dict[str, Any]:
        day = trade_date or datetime.now(CN_TZ).date()

        def read() -> dict[str, Any]:
            with database.transaction() as connection:
                return strategy_cards(connection, day, per_line=per_line)

        return await run_database_blocking(read, timeout_seconds=60)

    return router


__all__ = ["build_market_radar_router"]
