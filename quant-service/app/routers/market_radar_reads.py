"""Read-only research boards: radar, limit-up detail, strategy cards, indicator health, data sources, strategies."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Query

from ..market_radar import CN_TZ
from ..indicator_health import indicator_health
from ..indicator_registry import registry
from ..limit_detail_read_model import limit_detail_day
from ..market_radar_runtime import radar_day
from ..research_boards import datasource_board, strategy_board
from ..minute_cross_section_export import panel as minute_panel
from ..strategy_cards_read_model import strategy_cards


def build_market_radar_router(database: Any, run_database_blocking: Callable[..., Awaitable[Any]]) -> APIRouter:
    router = APIRouter(tags=["market-radar"])

    @router.get("/api/v1/market/radar")
    async def market_radar(trade_date: date | None = None, include_entered: bool = False,
                           segments: bool = False) -> dict[str, Any]:
        day = trade_date or datetime.now(CN_TZ).date()

        def read() -> dict[str, Any]:
            with database.transaction() as connection:
                return radar_day(connection, day, include_entered=include_entered, include_segments=segments)

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
                return strategy_cards(connection, day, per_line=per_line, now=datetime.now(CN_TZ))

        return await run_database_blocking(read, timeout_seconds=60)

    @router.get("/api/v1/indicators")
    async def indicator_registry_route() -> dict[str, Any]:
        return {"indicators": registry(), "research_only": True, "live_effect": "none"}

    @router.get("/api/v1/indicators/health")
    async def indicator_health_route(trade_date: date | None = None, keys: str | None = None) -> dict[str, Any]:
        now = datetime.now(CN_TZ)
        day = trade_date or now.date()
        wanted = [key.strip() for key in keys.split(",") if key.strip()] if keys else None

        def read() -> dict[str, Any]:
            with database.transaction() as connection:
                return indicator_health(connection, day, now, wanted)

        return await run_database_blocking(read, timeout_seconds=60)

    @router.get("/api/v1/market/minute-panel")
    async def minute_panel_route(trade_date: date, symbols: str, fields: str | None = None) -> dict[str, Any]:
        """Minute series of up to 50 symbols for one session (decision 0009); research, not polling."""
        wanted = [item for item in symbols.split(",") if item.strip()]
        chosen = [item.strip() for item in fields.split(",") if item.strip()] if fields else None

        def read() -> dict[str, Any]:
            return minute_panel(database, trade_date, wanted, chosen)

        return await run_database_blocking(read, timeout_seconds=120)

    @router.get("/api/v1/datasources/board")
    async def datasources_board_route() -> dict[str, Any]:
        def read() -> dict[str, Any]:
            with database.transaction() as connection:
                return datasource_board(connection, datetime.now(CN_TZ))

        return await run_database_blocking(read, timeout_seconds=30)

    @router.get("/api/v1/strategies/board")
    async def strategies_board_route() -> dict[str, Any]:
        def read() -> dict[str, Any]:
            with database.transaction() as connection:
                return strategy_board(connection, datetime.now(CN_TZ))

        return await run_database_blocking(read, timeout_seconds=30)

    return router


__all__ = ["build_market_radar_router"]
