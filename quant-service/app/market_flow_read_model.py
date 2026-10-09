"""Read-only projections for persisted multiscale market-flow evidence.

A session's daily concept flow is served from ``eastmoney_concept`` - 同花顺
boards keyed by name, read through akshare from data.10jqka.com.cn despite the
key - and from the Tushare-era ``ths_concept_flow`` only for a session that
has nothing newer; the projection names the taxonomy it served.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


#: Daily concept flow, live first; the frozen Tushare-era rows are history.
SECTOR_DAILY_TAXONOMIES = ("eastmoney_concept", "ths_concept_flow")
SECTOR_DAILY_SQL = """WITH chosen AS (
           SELECT taxonomy_key FROM quant.sector_flow_daily_features
            WHERE trading_date=%s AND taxonomy_key IN ('eastmoney_concept','ths_concept_flow')
            GROUP BY taxonomy_key
            ORDER BY CASE taxonomy_key WHEN 'eastmoney_concept' THEN 0 ELSE 1 END LIMIT 1
       )
       SELECT feature.taxonomy_key,feature.trading_date,feature.sector_key,sector.label,feature.provider_key,
              feature.status,feature.transition,feature.net_amount,feature.previous_net_amount,
              feature.net_change_amount,feature.net_acceleration,feature.rank_percentile,
              feature.flow_sign_streak,feature.change_pct,feature.price_flow_divergence,
              feature.lhb_stock_count,feature.lhb_net_amount,feature.lhb_negative_count,
              feature.lhb_sell_pressure_ratio,feature.limit_up_count,feature.quality_flags
         FROM quant.sector_flow_daily_features feature
         JOIN chosen ON chosen.taxonomy_key=feature.taxonomy_key
         JOIN quant.sectors sector
           ON sector.taxonomy_key=feature.taxonomy_key AND sector.sector_key=feature.sector_key
        WHERE feature.trading_date=%s
        ORDER BY feature.rank_percentile DESC NULLS LAST,abs(feature.net_change_amount) DESC NULLS LAST
        LIMIT 500"""
SECTOR_OUTCOME_SQL = """SELECT taxonomy_key,transition,horizon_days,count(*) FILTER (WHERE status='matured') AS matured,
              avg(directional_return) FILTER (WHERE status='matured') AS avg_directional_return,
              avg(cross_section_excess_return) FILTER (WHERE status='matured') AS avg_excess_return,
              avg((directional_return>0)::int) FILTER (WHERE status='matured') AS directional_hit_rate
         FROM quant.sector_flow_daily_outcomes
        GROUP BY taxonomy_key,transition,horizon_days
        ORDER BY taxonomy_key,horizon_days,transition"""


def sector_daily_source(sector_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Which concept-flow taxonomy a session's sector rows came from."""
    served = next((str(row.get("taxonomy_key")) for row in sector_rows if row.get("taxonomy_key")), None)
    if served is None:
        return {"status": "missing", "taxonomy_key": None, "requested_taxonomy_key": "ths_concept_flow",
                "reason": "no daily concept-flow features were materialized for the session"}
    return {"status": "served", "taxonomy_key": served, "requested_taxonomy_key": "ths_concept_flow",
            "source": "ths_10jqka_via_akshare" if served == "eastmoney_concept" else "tushare_moneyflow_cnt_ths",
            "sector_key": "board_name" if served == "eastmoney_concept" else "ths_code",
            "net_amount_unit": "100m_cny"}
def market_flow_features(
    database: Any,
    trade_date: date | None = None,
    *,
    limit: int = 720,
) -> dict[str, Any]:
    china = ZoneInfo("Asia/Shanghai")
    selected_date = trade_date or datetime.now(timezone.utc).astimezone(china).date()
    bounded_limit = max(1, min(int(limit), 1000))
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT feature_key,exchange_date,cadence,observed_at,source_snapshot_minute,status,market_state,
                      concept_count,concept_positive_ratio,concept_median_flow,concept_mean_change_pct,
                      five_minute_positive_ratio_delta,session_positive_ratio_delta,afternoon_repair_strength,
                      market_amount,market_volume,amount_change_pct,volume_change_pct,advancer_ratio,
                      features,quality_flags,updated_at
                 FROM quant.market_flow_feature_snapshots
                WHERE exchange_date=%s
                ORDER BY observed_at LIMIT %s""",
            (selected_date, bounded_limit),
        ).fetchall()
        daily_rows = connection.execute(
            """SELECT DISTINCT ON(exchange_date) feature_key,exchange_date,cadence,observed_at,status,market_state,
                      concept_count,concept_positive_ratio,market_amount,market_volume,amount_change_pct,
                      volume_change_pct,advancer_ratio,features,quality_flags
                 FROM quant.market_flow_feature_snapshots
                WHERE cadence IN ('close','midday')
                ORDER BY exchange_date DESC,
                         CASE cadence WHEN 'close' THEN 0 ELSE 1 END,observed_at DESC
                LIMIT 20""",
        ).fetchall()
        sector_rows = connection.execute(
            SECTOR_DAILY_SQL,
            (selected_date, selected_date),
        ).fetchall()
        outcome_rows = connection.execute(
            SECTOR_OUTCOME_SQL
        ).fetchall()
        readiness = connection.execute(
            """SELECT (SELECT count(DISTINCT trading_date) FROM quant.sector_flow_daily_features) AS trading_days,
                      count(DISTINCT (sector_key,signal_date)) FILTER (WHERE status='matured') AS matured_events
                 FROM quant.sector_flow_daily_outcomes"""
        ).fetchone()
    return project_market_flow_features(
        rows, daily_rows, sector_rows, outcome_rows, readiness, selected_date,
    )


def project_market_flow_features(
    rows: list[Any],
    daily_rows: list[Any],
    sector_rows: list[Any],
    outcome_rows: list[Any],
    readiness: Any,
    selected_date: date,
) -> dict[str, Any]:
    """Project already-read market-flow evidence without database access."""
    items = [dict(row) for row in rows]
    sector_daily = [dict(row) for row in sector_rows]
    state_counts: dict[str, int] = {}
    for item in items:
        state = str(item["market_state"])
        state_counts[state] = state_counts.get(state, 0) + 1
    return {
        "trade_date": str(selected_date),
        "timezone": "Asia/Shanghai",
        "items": items,
        "latest": items[-1] if items else None,
        "daily": [dict(row) for row in daily_rows],
        "sector_daily": sector_daily,
        "sector_daily_source": sector_daily_source(sector_daily),
        "sector_outcome_summary": [dict(row) for row in outcome_rows],
        "state_counts": state_counts,
        "research_gate": {
            "status": "eligible_for_review" if int(readiness["trading_days"] or 0) >= 60 and int(readiness["matured_events"] or 0) >= 200 else "accumulating",
            "observed_trading_days": int(readiness["trading_days"] or 0),
            "matured_independent_events": int(readiness["matured_events"] or 0),
            "minimum_trading_days": 60,
            "minimum_independent_events": 200,
            "live_strategy_effect": "none",
        },
        "notice": "分钟同花顺板块流（经 akshare，库内键名 eastmoney_*）、全A量能与盘后板块资金流保持分层；缺失不补零，当前仅用于研究与前端复盘。",
    }


__all__ = ["SECTOR_DAILY_SQL", "SECTOR_DAILY_TAXONOMIES", "SECTOR_OUTCOME_SQL", "market_flow_features",
           "project_market_flow_features", "sector_daily_source"]
