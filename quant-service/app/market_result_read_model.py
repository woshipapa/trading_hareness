"""Bounded local projections for market evidence, research overview, and result ledgers."""

from __future__ import annotations

from typing import Any, Callable, Iterable

from fastapi import HTTPException
from . import minute_cross_section


def tushare_raw(database: Any, api_name: str, provider: str | None, limit: int, offset: int,
                catalog: Iterable[str]) -> dict[str, Any]:
    if api_name not in catalog:
        raise HTTPException(status_code=404, detail="api_name is not in the enabled catalog")
    bounded_limit, bounded_offset = max(1, min(limit, 500)), max(0, offset)
    with database.transaction() as connection:
        condition, values = ("api_name=%s", [api_name]) if provider is None else ("api_name=%s AND provider_key=%s", [api_name, provider])
        rows = connection.execute(
            f"""SELECT provider_key,api_name,request_key,record_index,record_key,row_data,available_at,created_at
               FROM quant.tushare_raw_records WHERE {condition} ORDER BY available_at DESC,record_index LIMIT %s OFFSET %s""",
            (*values, bounded_limit, bounded_offset),
        ).fetchall()
        total = connection.execute(
            f"SELECT count(*)::int total FROM quant.tushare_raw_records WHERE {condition}", values,
        ).fetchone()["total"]
    return {"api_name": api_name, "provider": provider, "items": rows, "limit": bounded_limit, "offset": bounded_offset,
            "total": total, "next_offset": bounded_offset + len(rows) if bounded_offset + len(rows) < total else None}


def research_overview(
    database: Any,
    *,
    current_data_coverage_fn: Callable[[Any], dict[str, Any]],
    feature_readiness_fn: Callable[[Any], dict[str, Any]],
    history_estimate_fn: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    with database.transaction() as connection:
        counts = connection.execute(
            """SELECT (SELECT count(*)::int FROM quant.remote_reports) remote_reports,
                      (SELECT count(*)::int FROM quant.analyst_claims) claims,
                      (SELECT count(*)::int FROM quant.canonical_bars_daily) canonical_bars,
                      (SELECT count(*)::int FROM quant.tushare_raw_records) tushare_raw_records,
                      (SELECT count(*)::int FROM quant.market_trade_calendar) calendar_days,
                      (SELECT count(*)::int FROM quant.daily_fundamentals) fundamentals,
                      (SELECT count(*)::int FROM quant.daily_trade_limits) trade_limits,
                      (SELECT count(*)::int FROM quant.market_bars_minute) offline_minute_bars,
                      (SELECT count(*)::int FROM quant.market_snapshot_runs) market_snapshot_runs,
                      (SELECT count(*)::int FROM quant.market_events) market_events,
                      (SELECT count(*)::int FROM quant.universe_members WHERE universe_key='all_a' AND enabled) all_a_symbols,
                      (SELECT count(*)::int FROM quant.sectors) sectors,
                      (SELECT count(*)::int FROM quant.sector_membership_history WHERE effective_to IS NULL) active_sector_memberships,
                      (SELECT count(*)::int FROM quant.sector_market_observations) sector_market_observations,
                      (SELECT count(*)::int FROM quant.offline_imports WHERE status IN ('completed','partial')) offline_imports,
                      (SELECT count(*)::int FROM quant.fetch_runs WHERE status='running') running_fetch_runs,
                      (SELECT count(*)::int FROM quant.fetch_runs WHERE status='running' AND coalesce(started_at,created_at)<now()-interval '90 minutes') stale_fetch_runs,
                      (SELECT count(*)::int FROM quant.data_quality_issues WHERE resolved_at IS NULL) quality_issues"""
        ).fetchone()
        last_snapshot = connection.execute(
            "SELECT snapshot_key,as_of_date,knowledge_cutoff,status,manifest_version,code_sha,data_schema_version,manifest,finalized_at FROM quant.data_snapshots ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        latest_run = connection.execute(
            "SELECT run_id,as_of_date,model_version,market_regime,source_status,created_at FROM quant.recommendation_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        latest_market_snapshot = connection.execute(
            """SELECT session,exchange_date,observed_at,universe_key,universe_count,quote_count,coverage,status,decision_eligible,
                      source_summary,summary,quality_flags,updated_at
               FROM quant.market_snapshot_runs ORDER BY exchange_date DESC,observed_at DESC LIMIT 1"""
        ).fetchone()
        coverage = current_data_coverage_fn(connection)
        readiness = feature_readiness_fn(connection)
    return {
        "counts": counts, "latest_snapshot": last_snapshot, "latest_market_snapshot": latest_market_snapshot,
        "latest_recommendation_run": latest_run, "data_coverage": coverage, "history_estimate": history_estimate_fn(),
        "feature_readiness": readiness,
    }


def market_snapshots(database: Any, limit: int) -> dict[str, Any]:
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT session,exchange_date,observed_at,universe_key,universe_count,quote_count,coverage,status,decision_eligible,
                      source_summary,summary,quality_flags,created_at,updated_at
               FROM quant.market_snapshot_runs ORDER BY exchange_date DESC,observed_at DESC LIMIT %s""",
            (max(1, min(limit, 100)),),
        ).fetchall()
    return {"items": rows}


def _cross_section_payload(source: str, observed_at: Any, rows: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    items = minute_cross_section.latest_level1_items(observed_at, rows, limit)
    return {"status": "completed", "snapshot_at": observed_at, "items": items, "count": len(rows),
            "returned": len(items), "truncated": len(rows) > len(items), "research_only": True, "source": source}


def latest_all_a_level1(database: Any, limit: int = 6000) -> dict[str, Any]:
    """Return the newest complete raw all-A Level-1 capture: this process's memory, the minute document, the rows."""
    limit = max(1, min(int(limit), 6000))
    remembered = minute_cross_section.recent()
    if remembered is not None:
        return _cross_section_payload("memory", remembered[0], remembered[1], limit)
    with database.transaction() as connection:
        document = minute_cross_section.latest(connection)
        legacy = connection.execute(
            "SELECT max(effective_at) AS snapshot_at FROM quant.raw_market_observations WHERE capability='a_share_prices_snapshot'"
        ).fetchone()
        if document is not None and (legacy is None or legacy["snapshot_at"] is None or document[0] >= legacy["snapshot_at"]):
            return _cross_section_payload("document", document[0], minute_cross_section.rows_of(document[1]), limit)
        snapshot_at = legacy["snapshot_at"] if legacy else None
        if snapshot_at is None:
            return {"status": "empty", "snapshot_at": None, "items": [], "count": 0}
        rows = connection.execute(
            """SELECT symbol,effective_at,available_at,normalized,payload_sha256
                 FROM quant.raw_market_observations
                WHERE capability='a_share_prices_snapshot' AND effective_at=%s
                ORDER BY symbol LIMIT %s""", (snapshot_at, limit)
        ).fetchall()
        count = connection.execute(
            """SELECT count(*)::int AS count FROM quant.raw_market_observations
                 WHERE capability='a_share_prices_snapshot' AND effective_at=%s""", (snapshot_at,)
        ).fetchone()["count"]
    return {"status": "completed", "snapshot_at": snapshot_at, "items": rows,
            "count": count, "returned": len(rows), "truncated": count > len(rows),
            "research_only": True}


def offline_minute_imports(database: Any, limit: int, offline_directory: str) -> dict[str, Any]:
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT import_id,source_name,file_name,dataset_kind,status,row_count,rejected_rows,error_message,started_at,finished_at
               FROM quant.offline_imports ORDER BY started_at DESC LIMIT %s""",
            (max(1, min(limit, 100)),),
        ).fetchall()
    return {"items": rows, "offline_directory": offline_directory}


def analyst_scorecards(database: Any, limit: int, readiness_fn: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT analyst_id,horizon_days,as_of_date,observations,hit_rate,mean_excess_return,
                      mean_directional_return,calibration_score,methodology_version,created_at
                 FROM quant.analyst_scorecards
                 ORDER BY as_of_date DESC,observations DESC,analyst_id,horizon_days LIMIT %s""",
            (max(1, min(limit, 500)),),
        ).fetchall()
        readiness = readiness_fn(connection)
    return {"items": rows, "readiness": readiness,
            "notice": "成绩单只对方向明确、且后续价格路径已经成熟的股票观点计算。"}


def latest_recommendations(database: Any) -> dict[str, Any]:
    with database.transaction() as connection:
        run = connection.execute("SELECT * FROM quant.recommendation_runs ORDER BY created_at DESC LIMIT 1").fetchone()
        if not run:
            return {"run": None, "recommendations": []}
        rows = connection.execute("SELECT * FROM quant.recommendations WHERE run_id=%s ORDER BY rank LIMIT 500", (run["run_id"],)).fetchall()
    return {"run": run, "recommendations": rows}


def metrics(database: Any) -> Any:
    with database.transaction() as connection:
        return connection.execute(
            """SELECT (SELECT count(*) FROM quant.market_bars_daily) bars,
                      (SELECT count(*) FROM quant.analyst_signals) signals,
                      (SELECT count(*) FROM quant.remote_reports) remote_reports,
                      (SELECT count(*) FROM quant.analyst_claims) analyst_claims,
                      (SELECT count(*) FROM quant.tushare_raw_records) tushare_raw_records,
                      (SELECT count(*) FROM quant.market_bars_minute) offline_minute_bars,
                      (SELECT count(*) FROM quant.recommendation_runs) recommendation_runs"""
        ).fetchone()


__all__ = [
    "analyst_scorecards", "latest_recommendations", "market_snapshots", "metrics", "offline_minute_imports",
    "research_overview", "tushare_raw",
]
