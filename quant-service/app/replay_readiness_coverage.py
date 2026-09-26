"""Durable daily coverage projection for the replay-readiness control plane.

The raw daily/control tables contain several million rows.  Recomputing their
point-in-time intersection on every dashboard GET is both slow and capable of
starving normal API reads.  This module materializes one small row per exchange
date.  It is a derived control-plane projection only: it does not fetch data,
change strategy thresholds, or make an incomplete date eligible.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from typing import Any

from .database import Database
from .owner_storage import eligible_cold_tables, tiered_relation_sql


COVERAGE_DEFINITION = "point_in_time_all_a_membership_with_complete_adjusted_daily_bars_fundamentals_and_trade_limits_at_80pct_min_1000"

REFRESH_DAILY_COVERAGE_SQL = """WITH bounds AS (
        SELECT %s::date AS start_date,%s::date AS end_date
    ), eligible_bars AS MATERIALIZED (
        SELECT bars.trading_date,bars.symbol
          FROM quant.canonical_bars_daily bars CROSS JOIN bounds
         WHERE bars.symbol<>'000300.SH'
           AND bars.available_at < ((bars.trading_date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
           AND bars.quality_status='fresh'
           AND bars.adj_factor>0
           AND (bounds.start_date IS NULL OR bars.trading_date>=bounds.start_date)
           AND (bounds.end_date IS NULL OR bars.trading_date<=bounds.end_date)
    ), daily_dates AS (
        SELECT trading_date,count(*)::int AS bar_symbols
          FROM eligible_bars GROUP BY trading_date
    ), expected_universe AS (
        SELECT dates.trading_date,count(DISTINCT membership.symbol)::int AS expected_symbols
          FROM daily_dates dates
          JOIN quant.universe_membership_history membership
            ON membership.universe_key='all_a'
           AND membership.effective_from<=dates.trading_date
           AND (membership.effective_to IS NULL OR membership.effective_to>=dates.trading_date)
         GROUP BY dates.trading_date
    ), fundamental_counts AS (
        SELECT bars.trading_date,count(DISTINCT fundamentals.symbol)::int AS fundamental_symbols
          FROM eligible_bars bars
          JOIN quant.daily_fundamentals fundamentals
            ON fundamentals.symbol=bars.symbol AND fundamentals.trading_date=bars.trading_date
           AND fundamentals.available_at < ((fundamentals.trading_date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
         GROUP BY bars.trading_date
    ), limit_counts AS (
        SELECT bars.trading_date,count(DISTINCT limits.symbol)::int AS limit_symbols
          FROM eligible_bars bars
          JOIN quant.daily_trade_limits limits
            ON limits.symbol=bars.symbol AND limits.trading_date=bars.trading_date
           AND limits.available_at < ((limits.trading_date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
         GROUP BY bars.trading_date
    )
    INSERT INTO quant.replay_readiness_daily_coverage(
        trading_date,expected_symbols,bar_symbols,fundamental_symbols,limit_symbols,
        is_full_cross_section,coverage_definition,refreshed_at)
    SELECT dates.trading_date,coalesce(universe.expected_symbols,0),dates.bar_symbols,
           coalesce(fundamentals.fundamental_symbols,0),coalesce(limits.limit_symbols,0),
           coalesce(universe.expected_symbols,0)>=1000
             AND dates.bar_symbols>=greatest(ceil(coalesce(universe.expected_symbols,0)*0.8)::int,1000)
             AND coalesce(fundamentals.fundamental_symbols,0)>=greatest(ceil(coalesce(universe.expected_symbols,0)*0.8)::int,1000)
             AND coalesce(limits.limit_symbols,0)>=greatest(ceil(coalesce(universe.expected_symbols,0)*0.8)::int,1000),
           %s,now()
      FROM daily_dates dates
      LEFT JOIN expected_universe universe USING(trading_date)
      LEFT JOIN fundamental_counts fundamentals USING(trading_date)
      LEFT JOIN limit_counts limits USING(trading_date)
    ON CONFLICT(trading_date) DO UPDATE SET
      expected_symbols=EXCLUDED.expected_symbols,bar_symbols=EXCLUDED.bar_symbols,
      fundamental_symbols=EXCLUDED.fundamental_symbols,limit_symbols=EXCLUDED.limit_symbols,
      is_full_cross_section=EXCLUDED.is_full_cross_section,
      coverage_definition=EXCLUDED.coverage_definition,refreshed_at=EXCLUDED.refreshed_at"""

MATERIALIZED_DAILY_METRICS_SQL = f"""SELECT
      min(trading_date) first_daily_date,max(trading_date) latest_daily_date,
      min(trading_date) FILTER (WHERE is_full_cross_section) first_full_cross_section_date,
      max(trading_date) FILTER (WHERE is_full_cross_section) latest_full_cross_section_date,
      count(*) FILTER (WHERE is_full_cross_section)::int full_cross_section_days,
      count(*)::int daily_bar_days,
      count(*) FILTER (WHERE expected_symbols>0)::int point_in_time_universe_days,
      count(*) FILTER (WHERE expected_symbols=0)::int missing_point_in_time_universe_days,
      max(refreshed_at) daily_coverage_refreshed_at,
      'materialized_daily_coverage_v1'::text daily_coverage_source
    FROM quant.replay_readiness_daily_coverage
   WHERE coverage_definition={COVERAGE_DEFINITION!r}"""


def refresh_daily_coverage(
    connection: Any, start_date: date | None = None, end_date: date | None = None,
) -> dict[str, Any]:
    """Replace the bounded date slice and return a secret-free receipt."""
    if start_date and end_date and end_date < start_date:
        raise ValueError("end_date must not precede start_date")
    connection.execute(
        """DELETE FROM quant.replay_readiness_daily_coverage
             WHERE (%s::date IS NULL OR trading_date>=%s::date)
               AND (%s::date IS NULL OR trading_date<=%s::date)""",
        (start_date, start_date, end_date, end_date),
    )
    # Contract fakes intentionally expose only ``execute``/``fetchone``; the
    # real psycopg connection can perform the catalog checks. Cold reads stay
    # disabled until all five twins are schema-compatible and correctly placed.
    cold_tables = eligible_cold_tables(connection) if hasattr(connection, "cursor") else set()
    sql = REFRESH_DAILY_COVERAGE_SQL
    for logical_name in ("canonical_bars_daily", "daily_fundamentals", "daily_trade_limits"):
        sql = sql.replace(f"quant.{logical_name}", tiered_relation_sql(logical_name, cold_tables))
    result = connection.execute(
        sql,
        (start_date, end_date, COVERAGE_DEFINITION),
    )
    metrics = connection.execute(MATERIALIZED_DAILY_METRICS_SQL).fetchone()
    return {
        "status": "completed", "start_date": str(start_date) if start_date else None,
        "end_date": str(end_date) if end_date else None,
        "rows_refreshed": int(getattr(result, "rowcount", 0) or 0),
        "metrics": dict(metrics or {}), "research_only": True, "live_effect": "none",
    }


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Materialize replay daily coverage without provider access")
    parser.add_argument("--start-date", type=_date)
    parser.add_argument("--end-date", type=_date)
    args = parser.parse_args()
    database = Database()
    try:
        with database.transaction() as connection:
            result = refresh_daily_coverage(connection, args.start_date, args.end_date)
        print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
    finally:
        database.close()


if __name__ == "__main__":
    main()


__all__ = [
    "COVERAGE_DEFINITION", "MATERIALIZED_DAILY_METRICS_SQL", "REFRESH_DAILY_COVERAGE_SQL",
    "refresh_daily_coverage",
]
