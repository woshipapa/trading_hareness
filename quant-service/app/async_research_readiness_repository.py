"""Native async read projections for research-readiness control-plane data."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from .replay_readiness import (
    AUXILIARY_REPLAY_METRICS_SQL, DIRECT_DAILY_METRICS_SQL,
    READINESS_STATEMENT_TIMEOUT_MS, replay_readiness_payload,
)
from .replay_readiness_coverage import MATERIALIZED_DAILY_METRICS_SQL
from .research_capacity import feature_readiness_projection, historical_capacity_plan
from .research_framework_readiness import enrich_framework_rows


_HISTORICAL_SAMPLE_APIS = (
    "daily", "adj_factor", "daily_basic", "stk_limit", "moneyflow_dc",
    "moneyflow", "cyq_perf", "cyq_chips", "stk_factor_pro", "suspend_d",
)

_HISTORICAL_UNIVERSE_SQL = """SELECT coalesce(
        nullif((SELECT count(*)::int FROM quant.universe_members
                 WHERE universe_key='all_a' AND enabled),0),
        nullif((SELECT count(*)::int FROM quant.instruments
                 WHERE symbol ~ '^\\d{6}\\.(SH|SZ|BJ)$'),0),5500
    ) symbols"""

_HISTORICAL_SAMPLES_SQL = """WITH requested(api_name) AS (SELECT unnest(%s::text[])),
         sampled AS (
             SELECT requested.api_name, raw.row_data
               FROM requested
               CROSS JOIN LATERAL (
                   SELECT row_data
                     FROM quant.tushare_raw_records
                    WHERE api_name=requested.api_name
                    ORDER BY available_at DESC
                    LIMIT 256
               ) raw
         )
    SELECT api_name,ceil(avg(pg_column_size(row_data)))::int avg_bytes
      FROM sampled GROUP BY api_name"""

_HISTORICAL_BARS_COVERAGE_SQL = """WITH daily_counts AS (
         SELECT trading_date,count(*)::int symbols
           FROM quant.canonical_bars_daily
          WHERE symbol<>'000300.SH' AND adj_factor>0
          GROUP BY trading_date
       ), universe AS (
         SELECT greatest(1,(SELECT count(*)::int FROM quant.universe_members
                            WHERE universe_key='all_a' AND enabled)) AS symbols
       )
       SELECT (SELECT min(trading_date) FROM quant.canonical_bars_daily
                WHERE adj_factor>0) first_bar_date,
              (SELECT max(trading_date) FROM quant.canonical_bars_daily
                WHERE adj_factor>0) latest_bar_date,
              (SELECT count(*)::int FROM daily_counts) bar_days,
              (SELECT count(*)::int FROM daily_counts,universe
                WHERE daily_counts.symbols>=greatest(ceil(universe.symbols*0.8)::int,1000))
                full_cross_section_days,
              (SELECT max(symbols) FROM daily_counts) max_symbols_on_day"""

_HISTORICAL_FUNDAMENTALS_SQL = """SELECT count(DISTINCT symbol)::int fundamental_symbols
                                     FROM quant.daily_fundamentals"""
_HISTORICAL_LIMITS_SQL = """SELECT count(DISTINCT symbol)::int limit_symbols
                               FROM quant.daily_trade_limits"""
_HISTORICAL_MINUTES_SQL = """SELECT count(DISTINCT symbol)::int minute_symbols
                                FROM quant.market_bars_minute"""

# These aggregates are independent read projections.  Keeping one SQL string
# per feature lets the async pool overlap the large history scans instead of
# waiting for the UNION's slowest branch before starting the next one.  Every
# query returns the same row shape consumed by feature_readiness_projection.
_FEATURE_READINESS_SQL = (
    """SELECT 'daily_bars' AS feature,
                     count(DISTINCT symbol)::int AS symbols,
                     count(*)::int AS rows,
                     max(trading_date) AS latest_date,
                     'P0' AS priority
                FROM quant.canonical_bars_daily
               WHERE symbol <> '000300.SH' AND adj_factor > 0""",
    """SELECT 'daily_basic' AS feature,
                     count(DISTINCT symbol)::int AS symbols,
                     count(*)::int AS rows,
                     max(trading_date) AS latest_date,
                     'P0' AS priority
                FROM quant.daily_fundamentals""",
    """SELECT 'trade_limits' AS feature,
                     count(DISTINCT symbol)::int AS symbols,
                     count(*)::int AS rows,
                     max(trading_date) AS latest_date,
                     'P0' AS priority
                FROM quant.daily_trade_limits""",
    """SELECT 'stock_flow' AS feature,
                     count(DISTINCT symbol)::int AS symbols,
                     count(*)::int AS rows,
                     max(trading_date) AS latest_date,
                     'P1' AS priority
                FROM quant.stock_money_flow_daily
               WHERE source = 'longhuvip_main_net'""",
    """SELECT 'stk_factor_pro' AS feature,
                     count(DISTINCT row_data->>'ts_code')::int AS symbols,
                     count(*)::int AS rows,
                     max(to_date(NULLIF(row_data->>'trade_date', ''), 'YYYYMMDD')) AS latest_date,
                     'P1' AS priority
                FROM quant.tushare_raw_records
               WHERE api_name = 'stk_factor_pro'""",
    """SELECT 'sector_flow' AS feature,
                     count(DISTINCT sector_key)::int AS symbols,
                     count(*)::int AS rows,
                     max(trading_date) AS latest_date,
                     'P1' AS priority
                FROM quant.sector_market_observations""",
    """SELECT 'announcements' AS feature,
                     count(DISTINCT symbol)::int AS symbols,
                     count(*)::int AS rows,
                     max(occurred_at::date) AS latest_date,
                     'P1' AS priority
                FROM quant.market_events""",
    """SELECT 'analyst_claims' AS feature,
                     count(DISTINCT subject_key)::int AS symbols,
                     count(*)::int AS rows,
                     max((available_at AT TIME ZONE 'Asia/Shanghai')::date) AS latest_date,
                     'P1' AS priority
                FROM quant.analyst_claims""",
)

_FEATURE_READINESS_UNIVERSE_SQL = """SELECT greatest(1, count(*)::int) AS symbols
                                         FROM quant.universe_members
                                        WHERE universe_key = 'all_a' AND enabled"""


async def _fetchone(async_database: Any, sql: str, params: Any = ()) -> dict[str, Any]:
    """Run one independent read in its own transaction/pooled connection."""
    async with async_database.transaction() as connection:
        result = await connection.execute(sql, params)
        return dict(await result.fetchone() or {})


async def _fetchall(async_database: Any, sql: str, params: Any = ()) -> list[dict[str, Any]]:
    """Run one bounded list read in its own transaction/pooled connection."""
    async with async_database.transaction() as connection:
        result = await connection.execute(sql, params)
        return [dict(row) for row in await result.fetchall()]


async def frameworks(async_database: Any) -> dict[str, Any]:
    async with async_database.transaction() as connection:
        result = await connection.execute(
            "SELECT framework_key,label,role,integration_mode,status,license_note,prerequisites,metadata,updated_at FROM quant.research_frameworks ORDER BY framework_key"
        )
        rows = await result.fetchall()
    return {
        "items": enrich_framework_rows([dict(row) for row in rows]),
        "research_only": True,
        "live_effect": "none",
    }


async def feature_readiness(async_database: Any) -> dict[str, Any]:
    # These are display-only aggregates.  PostgreSQL READ COMMITTED gives
    # each statement the same statement-level snapshot semantics as the old
    # UNION plus universe query, while separate pooled connections let the
    # expensive history scans use available CPU, storage and tunnel capacity
    # concurrently.  The pool remains bounded by ASYNC_POOL_MAX_WAITING.
    rows, universe = await asyncio.gather(
        asyncio.gather(*(_fetchone(async_database, sql) for sql in _FEATURE_READINESS_SQL)),
        _fetchone(async_database, _FEATURE_READINESS_UNIVERSE_SQL),
    )
    return feature_readiness_projection(rows, int(universe.get("symbols") or 1))


async def replay_readiness(async_database: Any) -> dict[str, Any]:
    """Read bounded replay gates using the native async connection."""
    async with async_database.transaction() as connection:
        try:
            await connection.execute(f"SET LOCAL statement_timeout = '{READINESS_STATEMENT_TIMEOUT_MS}ms'")
            result = await connection.execute(
                "SELECT to_regclass('quant.replay_readiness_daily_coverage') AS value"
            )
            table = await result.fetchone()
            daily: dict[str, Any] = {}
            table_value = table.get("value") if isinstance(table, Mapping) else None
            if table_value is not None:
                result = await connection.execute(MATERIALIZED_DAILY_METRICS_SQL)
                daily = dict(await result.fetchone() or {})
                if int(daily.get("daily_bar_days") or 0) <= 0:
                    # Never turn a stale projection into a full historical
                    # scan on an async dashboard connection.  The bounded
                    # refresh job must rebuild the current coverage contract.
                    return replay_readiness_payload({"readiness_query_status": "coverage_stale"})
            else:
                result = await connection.execute(DIRECT_DAILY_METRICS_SQL)
                daily = dict(await result.fetchone() or {})
            result = await connection.execute(AUXILIARY_REPLAY_METRICS_SQL)
            auxiliary = dict(await result.fetchone() or {})
            metrics = {**daily, **auxiliary, "readiness_query_status": "ok"}
        except Exception as error:
            status = "timeout" if type(error).__name__ in {"QueryCanceled", "QueryCanceledError"} else "error"
            return replay_readiness_payload({"readiness_query_status": status})
    return replay_readiness_payload(metrics)


async def historical_estimate(async_database: Any, request: Any) -> dict[str, Any]:
    """Estimate local research capacity without a synchronous DB handoff."""
    # Each query is read-only and independent.  PostgreSQL's READ COMMITTED
    # transaction already gives every statement its own snapshot; using one
    # pooled connection per aggregate preserves the values while allowing the
    # CPU, storage and owner tunnel to overlap the expensive scans.
    universe_task = (
        asyncio.sleep(0, result=int(request.universe_symbols))
        if request.universe_symbols
        else _fetchone(async_database, _HISTORICAL_UNIVERSE_SQL)
    )
    universe, samples, sectors, bars, fundamentals, limits, minutes = await asyncio.gather(
        universe_task,
        _fetchall(async_database, _HISTORICAL_SAMPLES_SQL, ([*_HISTORICAL_SAMPLE_APIS],)),
        _fetchone(async_database, "SELECT count(*)::int total FROM quant.sectors"),
        _fetchone(async_database, _HISTORICAL_BARS_COVERAGE_SQL),
        _fetchone(async_database, _HISTORICAL_FUNDAMENTALS_SQL),
        _fetchone(async_database, _HISTORICAL_LIMITS_SQL),
        _fetchone(async_database, _HISTORICAL_MINUTES_SQL),
    )
    universe_symbols = int(universe if isinstance(universe, int) else universe.get("symbols") or 5500)
    coverage = {
        **bars,
        **fundamentals,
        **limits,
        **minutes,
    }
    return {
        **historical_capacity_plan(
            request.years, universe_symbols, request.trading_days_per_year, request.include_minute,
            {row["api_name"]: row["avg_bytes"] for row in samples}, int(sectors.get("total") or 0),
        ),
        "current_coverage": coverage,
        "assumptions": {
            "storage_multiplier": 1.35,
            "row_size_source": "newest 256 raw samples per API when present, otherwise conservative constants",
            "minute_policy": "not included unless include_minute=true; historical minute remains offline-file only",
        },
    }
