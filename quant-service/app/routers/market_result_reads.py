"""Read-only routes for materialized market/research results."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from fastapi import APIRouter

from .. import market_result_read_model as read_model
from .. import async_market_result_read_repository as async_read_model
from ..async_keyed_memo import AsyncKeyedMemo
from ..runtime_executors import run_database_blocking


def build_market_result_reads_router(
    database: Any,
    catalog: Iterable[str],
    current_data_coverage_fn: Callable[..., dict[str, Any]],
    feature_readiness_fn: Callable[..., dict[str, Any]],
    history_estimate_fn: Callable[[], dict[str, Any]],
    offline_root_fn: Callable[[], Path],
    analyst_scorecard_readiness_fn: Callable[..., dict[str, Any]],
    async_database: Any | None = None,
    async_history_estimate_fn: Callable[[], Awaitable[dict[str, Any]]] | None = None,
) -> APIRouter:
    router = APIRouter(tags=["market-result-reads"])

    async def read_overview() -> dict[str, Any]:
        if async_database:
            if async_history_estimate_fn is None:
                # The legacy history projection is synchronous, but the native
                # overview read can still use the async pool at the same time.
                history_task = asyncio.create_task(
                    run_database_blocking(history_estimate_fn, timeout_seconds=15),
                )
            else:
                history_task = asyncio.create_task(async_history_estimate_fn())
            overview_task = asyncio.create_task(async_read_model.research_overview(async_database, {}))
            tasks = (overview_task, history_task)
            try:
                overview_payload, history_estimate = await asyncio.wait_for(
                    asyncio.gather(*tasks), timeout=15,
                )
            except BaseException:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            overview_payload["history_estimate"] = history_estimate
            return overview_payload

        def read_sync_overview() -> dict[str, Any]:
            return read_model.research_overview(
                database, current_data_coverage_fn=current_data_coverage_fn,
                feature_readiness_fn=feature_readiness_fn,
                history_estimate_fn=history_estimate_fn,
            )

        return await run_database_blocking(read_sync_overview, timeout_seconds=15)

    # Dashboard panels commonly request the same overview at once.  Share only
    # the active read so a burst uses one bounded set of DB transactions; every
    # later request still observes fresh data.
    overview_inflight = AsyncKeyedMemo(
        lambda _key: read_overview(), ttl_seconds=0, cacheable=lambda _value: False, max_entries=1,
    )

    @router.get("/api/v1/providers/tushare/raw")
    async def raw(api_name: str, provider: str | None = None, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        return await async_read_model.tushare_raw(async_database, api_name, provider, limit, offset, catalog) if async_database else read_model.tushare_raw(database, api_name, provider, limit, offset, catalog)

    @router.get("/api/v1/research/overview")
    async def overview() -> dict[str, Any]:
        return await overview_inflight.get("all")

    @router.get("/api/v1/market/snapshots")
    async def snapshots(limit: int = 20) -> dict[str, Any]:
        return await async_read_model.market_snapshots(async_database, limit) if async_database else read_model.market_snapshots(database, limit)

    @router.get("/api/v1/market/level1/latest")
    async def latest_level1(limit: int = 6000) -> dict[str, Any]:
        return await async_read_model.latest_all_a_level1(async_database, limit) if async_database else read_model.latest_all_a_level1(database, limit)

    @router.get("/api/v1/market/minute/imports")
    async def minute_imports(limit: int = 30) -> dict[str, Any]:
        return await async_read_model.offline_minute_imports(async_database, limit, str(offline_root_fn())) if async_database else read_model.offline_minute_imports(database, limit, str(offline_root_fn()))

    @router.get("/api/v1/analyst-scorecards")
    async def scorecards(limit: int = 200) -> dict[str, Any]:
        if async_database:
            return await async_read_model.analyst_scorecards(async_database, limit)
        return read_model.analyst_scorecards(database, limit, analyst_scorecard_readiness_fn)

    @router.get("/api/v1/recommendations/latest")
    async def recommendations() -> dict[str, Any]:
        return await async_read_model.latest_recommendations(async_database) if async_database else read_model.latest_recommendations(database)

    @router.get("/api/v1/metrics")
    async def metric_counts() -> Any:
        return await async_read_model.metrics(async_database) if async_database else read_model.metrics(database)

    return router
