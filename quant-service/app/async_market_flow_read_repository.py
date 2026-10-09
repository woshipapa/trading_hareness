"""Native-async projection for stored multiscale market-flow evidence."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .market_flow_read_model import SECTOR_DAILY_SQL, SECTOR_OUTCOME_SQL, project_market_flow_features


async def market_flow_features(async_database: Any, trade_date: date | None = None, *, limit: int = 720) -> dict[str, Any]:
    china = ZoneInfo("Asia/Shanghai")
    selected_date = trade_date or datetime.now(timezone.utc).astimezone(china).date()
    bounded_limit = max(1, min(int(limit), 1000))
    async with async_database.transaction() as connection:
        rows_result = await connection.execute(
            """SELECT feature_key,exchange_date,cadence,observed_at,source_snapshot_minute,status,market_state,
                      concept_count,concept_positive_ratio,concept_median_flow,concept_mean_change_pct,
                      five_minute_positive_ratio_delta,session_positive_ratio_delta,afternoon_repair_strength,
                      market_amount,market_volume,amount_change_pct,volume_change_pct,advancer_ratio,
                      features,quality_flags,updated_at
                 FROM quant.market_flow_feature_snapshots
                WHERE exchange_date=%s
                ORDER BY observed_at LIMIT %s""", (selected_date, bounded_limit),
        )
        daily_result = await connection.execute(
            """SELECT DISTINCT ON(exchange_date) feature_key,exchange_date,cadence,observed_at,status,market_state,
                      concept_count,concept_positive_ratio,market_amount,market_volume,amount_change_pct,
                      volume_change_pct,advancer_ratio,features,quality_flags
                 FROM quant.market_flow_feature_snapshots
                WHERE cadence IN ('close','midday')
                ORDER BY exchange_date DESC,
                         CASE cadence WHEN 'close' THEN 0 ELSE 1 END,observed_at DESC
                LIMIT 20"""
        )
        sector_result = await connection.execute(
            SECTOR_DAILY_SQL, (selected_date, selected_date),
        )
        outcomes_result = await connection.execute(
            SECTOR_OUTCOME_SQL
        )
        readiness_result = await connection.execute(
            """SELECT (SELECT count(DISTINCT trading_date) FROM quant.sector_flow_daily_features) AS trading_days,
                      count(DISTINCT (sector_key,signal_date)) FILTER (WHERE status='matured') AS matured_events
                 FROM quant.sector_flow_daily_outcomes"""
        )
        rows = [dict(row) for row in await rows_result.fetchall()]
        daily_rows = [dict(row) for row in await daily_result.fetchall()]
        sector_rows = [dict(row) for row in await sector_result.fetchall()]
        outcome_rows = [dict(row) for row in await outcomes_result.fetchall()]
        readiness = await readiness_result.fetchone()
    return project_market_flow_features(rows, daily_rows, sector_rows, outcome_rows, readiness or {}, selected_date)


__all__ = ["market_flow_features"]
