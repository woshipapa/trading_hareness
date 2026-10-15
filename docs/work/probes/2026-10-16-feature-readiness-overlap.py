"""Compare the feature-readiness aggregate read paths on local compose.

Run this from the quant image with the local compose PostgreSQL service.  It
only reads persisted research evidence; it does not contact a provider or
write a row.  The legacy query is included as a same-process baseline so
cache and tunnel conditions are comparable.
"""

from __future__ import annotations

import asyncio
import statistics
import time

import app.async_research_readiness_repository as readiness
from app.async_research_readiness_repository import feature_readiness
from app.database import AsyncDatabase, Database


LEGACY_SQL = """SELECT 'daily_bars' feature,count(DISTINCT symbol)::int symbols,count(*)::int rows,max(trading_date) latest_date,'P0' priority FROM quant.canonical_bars_daily WHERE symbol<>'000300.SH' AND adj_factor>0
UNION ALL SELECT 'daily_basic',count(DISTINCT symbol)::int,count(*)::int,max(trading_date),'P0' FROM quant.daily_fundamentals
UNION ALL SELECT 'trade_limits',count(DISTINCT symbol)::int,count(*)::int,max(trading_date),'P0' FROM quant.daily_trade_limits
UNION ALL SELECT 'stock_flow',count(DISTINCT symbol)::int,count(*)::int,max(trading_date),'P1' FROM quant.stock_money_flow_daily WHERE source='longhuvip_main_net'
UNION ALL SELECT 'stk_factor_pro',count(DISTINCT row_data->>'ts_code')::int,count(*)::int,max(to_date(NULLIF(row_data->>'trade_date',''),'YYYYMMDD')),'P1' FROM quant.tushare_raw_records WHERE api_name='stk_factor_pro'
UNION ALL SELECT 'sector_flow',count(DISTINCT sector_key)::int,count(*)::int,max(trading_date),'P1' FROM quant.sector_market_observations
UNION ALL SELECT 'announcements',count(DISTINCT symbol)::int,count(*)::int,max(occurred_at::date),'P1' FROM quant.market_events
UNION ALL SELECT 'analyst_claims',count(DISTINCT subject_key)::int,count(*)::int,max((available_at AT TIME ZONE 'Asia/Shanghai')::date),'P1' FROM quant.analyst_claims"""
UNIVERSE_SQL = "SELECT greatest(1,count(*)::int) symbols FROM quant.universe_members WHERE universe_key='all_a' AND enabled"


async def legacy_read(database: AsyncDatabase) -> None:
    async with database.transaction() as connection:
        result = await connection.execute(LEGACY_SQL)
        await result.fetchall()
        result = await connection.execute(UNIVERSE_SQL)
        await result.fetchone()


async def main() -> None:
    database = AsyncDatabase(Database())
    parallel_samples: list[float] = []
    legacy_samples: list[float] = []
    payload = None
    calls: list[str] = []
    original_fetchone = readiness._fetchone

    async def counted_fetchone(database_arg, sql, params=()):
        calls.append(sql)
        return await original_fetchone(database_arg, sql, params)

    readiness._fetchone = counted_fetchone
    try:
        for _ in range(3):
            started = time.perf_counter()
            payload = await feature_readiness(database)
            parallel_samples.append((time.perf_counter() - started) * 1000)
        for _ in range(3):
            started = time.perf_counter()
            await legacy_read(database)
            legacy_samples.append((time.perf_counter() - started) * 1000)
    finally:
        readiness._fetchone = original_fetchone
        await database.close()

    assert payload is not None
    print({
        "parallel_read_count": len(readiness._FEATURE_READINESS_SQL) + 1,
        "parallel_statement_calls": len(calls),
        "parallel_elapsed_ms": [round(value, 3) for value in parallel_samples],
        "parallel_median_ms": round(statistics.median(parallel_samples), 3),
        "legacy_statement_count": 2,
        "legacy_elapsed_ms": [round(value, 3) for value in legacy_samples],
        "legacy_median_ms": round(statistics.median(legacy_samples), 3),
        "items": len(payload["items"]),
        "decision_ready": payload["decision_ready"],
        "scope": "local compose PostgreSQL; no owner tunnel/provider access",
    })


if __name__ == "__main__":
    asyncio.run(main())
