"""Read-only routes for stored research catalog and experiment evidence."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from uuid import UUID
from zoneinfo import ZoneInfo

from .. import research_catalog_read_model as read_model
from .. import async_research_catalog_read_repository as async_read_model
from ..read_cache import TTLCache


def build_research_catalog_reads_router(database: Any, async_database: Any | None = None) -> APIRouter:
    router = APIRouter(tags=["research-catalog-reads"])
    # Factor evaluations change after the close; one read serves the console for two minutes.
    evaluations_cache = TTLCache(120.0)

    @router.get("/api/v1/universes/{universe_key}")
    async def universe(universe_key: str) -> dict[str, Any]:
        return await async_read_model.universe_members(async_database, universe_key) if async_database else read_model.universe_members(database, universe_key)

    @router.get("/api/v1/features/latest")
    async def features(universe_key: str = "core", limit: int = 200) -> dict[str, Any]:
        return await async_read_model.latest_features(async_database, universe_key, limit) if async_database else read_model.latest_features(database, universe_key, limit)

    @router.get("/api/v1/factors")
    async def factors() -> dict[str, Any]:
        return await async_read_model.factor_registry(async_database) if async_database else read_model.factor_registry(database)

    @router.get("/api/v1/factors/evaluations")
    async def factor_evaluation_history(universe_key: str = "core", limit: int = 100) -> dict[str, Any]:
        async def compute() -> dict[str, Any]:
            return await async_read_model.factor_evaluations(async_database, universe_key, limit) if async_database else read_model.factor_evaluations(database, universe_key, limit)
        return await evaluations_cache.get((universe_key, limit), compute)

    @router.get("/api/v1/strategies")
    async def strategies() -> dict[str, Any]:
        return await async_read_model.strategy_registry(async_database) if async_database else read_model.strategy_registry(database)

    @router.get("/api/v1/research/models")
    async def models() -> dict[str, Any]:
        return await async_read_model.model_registry(async_database) if async_database else read_model.model_registry(database)

    @router.get("/api/v1/research/trials")
    async def research_trials(family: str | None = None, limit: int = 200) -> dict[str, Any]:
        """Latest evaluation of every research variant, deflated for its family's trial count."""
        return await async_read_model.research_trials(async_database, family, limit) if async_database else read_model.research_trials(database, family, limit)

    @router.get("/api/v1/research/regime-strata")
    async def research_regime_strata(as_of_date: date | None = None, strategy_key: str | None = None) -> dict[str, Any]:
        """Settled candidate-ledger outcomes by the index regime and sentiment stage of their signal date."""
        day = as_of_date or datetime.now(ZoneInfo("Asia/Shanghai")).date()
        return await async_read_model.regime_strata(async_database, day, strategy_key) if async_database else read_model.regime_strata(database, day, strategy_key)

    @router.get("/api/v1/strategies/experiments")
    async def experiments(universe_key: str = "core", limit: int = 50) -> dict[str, Any]:
        return await async_read_model.strategy_experiments(async_database, universe_key, limit) if async_database else read_model.strategy_experiments(database, universe_key, limit)

    @router.get("/api/v1/data-quality/issues")
    async def quality_issues(limit: int = 100) -> dict[str, Any]:
        return await async_read_model.data_quality_issues(async_database, limit) if async_database else read_model.data_quality_issues(database, limit)

    @router.get("/api/v1/strategy/daily-summary/latest")
    async def latest_daily_summary(exchange_date: date | None = None) -> dict[str, Any]:
        return await async_read_model.latest_strategy_day_summary(async_database, exchange_date) if async_database else read_model.latest_strategy_day_summary(database, exchange_date)

    @router.get("/api/v1/research/runs")
    async def research_run_history(
        experiment_type: str | None = None, status: str | None = None, limit: int = 50,
    ) -> dict[str, Any]:
        return await async_read_model.research_runs(async_database, experiment_type, status, limit) if async_database else read_model.research_runs(database, experiment_type, status, limit)

    @router.get("/api/v1/research/runs/{research_run_id}")
    async def research_run_detail(research_run_id: UUID) -> dict[str, Any]:
        payload = await async_read_model.research_run(async_database, research_run_id) if async_database else read_model.research_run(database, research_run_id)
        if payload["run"] is None:
            raise HTTPException(status_code=404, detail="research run not found")
        return payload

    return router
