"""Local-only factor experiment, backtest record and data-snapshot writes.

These operations consume the existing canonical database only.  They do not
fetch market providers, start historical ingestion, adjust strategy thresholds
or promote analyst weights.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

from .point_in_time import availability_cutoff, exchange_day_end
from .research_manifest import MANIFEST_VERSION, manifest_digest, snapshot_key as research_snapshot_key
from .research_run_repository import finish_research_run, start_research_run


FACTOR_INPUT_DATASETS = (
    "canonical_bars_daily", "market_trade_calendar", "universe_membership_history",
    "instruments", "sector_membership_history", "daily_adjustment_factors", "daily_fundamentals", "factor_registry",
)
STRATEGY_INPUT_DATASETS = (
    "canonical_bars_daily", "market_trade_calendar", "universe_membership_history",
    "instruments", "sector_membership_history", "daily_adjustment_factors", "daily_fundamentals", "strategy_registry",
)


@dataclass(frozen=True)
class ResearchExperimentDependencies:
    database: Any
    china_today: Callable[[], date]
    as_utc: Callable[[datetime], datetime]
    http_exception: type[Exception]
    evaluate_factor_set: Callable[..., list[dict[str, Any]]]
    run_multi_factor_strategy: Callable[..., dict[str, Any]]
    json_value: Callable[[Any], Any]


def research_window(
    connection: Any,
    universe_key: str,
    start_date: date | None,
    end_date: date | None,
    *,
    http_exception: type[Exception],
) -> tuple[date, date]:
    """Bound an experiment to persisted point-in-time universe membership."""
    bounds = connection.execute(
        """SELECT min(b.trading_date) earliest,max(b.trading_date) latest FROM quant.canonical_bars_daily b
           JOIN quant.universe_membership_history membership ON membership.symbol=b.symbol
            AND membership.universe_key=%s AND membership.effective_from<=b.trading_date
            AND (membership.effective_to IS NULL OR membership.effective_to>=b.trading_date)""",
        (universe_key,),
    ).fetchone()
    if not bounds or not bounds["latest"]:
        raise http_exception(status_code=422, detail="universe has no canonical daily bars")
    end = min(end_date or bounds["latest"], bounds["latest"])
    start = start_date or max(bounds["earliest"], end - timedelta(days=730))
    if start >= end:
        raise http_exception(status_code=422, detail="research window must contain at least two dates")
    return start, end


def latest_data_manifest_id(connection: Any, as_of_date: date, knowledge_cutoff: datetime) -> str | None:
    """Resolve the latest non-building manifest covering an experiment end date."""
    row = connection.execute(
        """SELECT snapshot_key
              FROM quant.data_snapshots
             WHERE as_of_date=%s AND knowledge_cutoff<=%s AND status IN ('ready','blocked')
             ORDER BY CASE status WHEN 'ready' THEN 0 ELSE 1 END,
                      knowledge_cutoff DESC,created_at DESC
             LIMIT 1""",
        (as_of_date, knowledge_cutoff),
    ).fetchone()
    if not row:
        return None
    value = row["snapshot_key"]
    return str(value) if value else None


def evaluate_factors(payload: Any, deps: ResearchExperimentDependencies) -> dict[str, Any]:
    pending_error: Exception | None = None
    with deps.database.transaction() as connection:
        start, end = research_window(
            connection, payload.universe_key, payload.start_date, payload.end_date,
            http_exception=deps.http_exception,
        )
        rows = connection.execute(
            "SELECT factor_key FROM quant.factor_registry WHERE implementation='native_sql' AND status<>'disabled' ORDER BY factor_key"
        ).fetchall()
        enabled = {str(row["factor_key"]) for row in rows}
        requested = payload.factor_keys or sorted(enabled)
        unknown = sorted(set(requested) - enabled)
        if unknown:
            raise deps.http_exception(status_code=422, detail=f"unknown or disabled factors: {', '.join(unknown)}")
        knowledge_cutoff = deps.as_utc(exchange_day_end(end))
        data_manifest_id = latest_data_manifest_id(connection, end, knowledge_cutoff)
        research_run_id = start_research_run(
            connection,
            experiment_type="factor_evaluation",
            universe_key=payload.universe_key,
            start_date=start,
            end_date=end,
            knowledge_cutoff=knowledge_cutoff,
            parameters={"factor_keys": requested, "horizon_days": payload.horizon_days},
            data_manifest_id=data_manifest_id,
            input_datasets=FACTOR_INPUT_DATASETS,
            data_schema_version="factor-evaluation-v1",
            json_value=deps.json_value,
        )
        try:
            evaluated = deps.evaluate_factor_set(connection, requested, payload.universe_key, start, end, payload.horizon_days)
        except ValueError as error:
            # Finish before leaving the transaction.  The exception is raised
            # only after the transaction commits, otherwise PostgreSQL would
            # roll the failed-run evidence back with the request.
            finish_research_run(connection, research_run_id, status="failed", error_message=str(error)[:1000], json_value=deps.json_value)
            pending_error = deps.http_exception(status_code=422, detail=str(error))
            evaluated = []
        if pending_error is None:
            results = []
            for result in evaluated:
                factor_key = str(result["factor_key"])
                row = connection.execute(
                    """INSERT INTO quant.factor_evaluations(factor_key,universe_key,start_date,end_date,horizon_days,engine,status,observations,
                        cross_section_days,metrics,artifact,research_run_id) VALUES(%s,%s,%s,%s,%s,'native_factor_sql_v2',%s,%s,%s,%s,%s,%s) RETURNING evaluation_id""",
                    (factor_key, payload.universe_key, start, end, payload.horizon_days, result["status"], result["observations"],
                     result["cross_section_days"], deps.json_value(result["metrics"]), deps.json_value(result["artifact"]), research_run_id),
                ).fetchone()
                result["evaluation_id"] = str(row["evaluation_id"])
                results.append(result)
            output_digest = finish_research_run(
                connection, research_run_id, status="completed",
                output={"results": results, "data_manifest_id": data_manifest_id}, json_value=deps.json_value,
            )
    if pending_error is not None:
        raise pending_error
    return {"research_run_id": str(research_run_id), "output_digest": output_digest,
            "data_manifest_id": data_manifest_id,
            "universe_key": payload.universe_key, "start_date": str(start), "end_date": str(end), "results": results}


def backtest_strategy(payload: Any, deps: ResearchExperimentDependencies) -> dict[str, Any]:
    pending_error: Exception | None = None
    with deps.database.transaction() as connection:
        registry = connection.execute(
            "SELECT strategy_key,version,configuration FROM quant.strategy_registry WHERE strategy_key=%s AND status<>'disabled'",
            (payload.strategy_key,),
        ).fetchone()
        if not registry:
            raise deps.http_exception(status_code=404, detail="strategy is not available")
        start, end = research_window(
            connection, payload.universe_key, payload.start_date, payload.end_date,
            http_exception=deps.http_exception,
        )
        parameters = {
            **dict(registry["configuration"]), "rebalance_days": payload.rebalance_days,
            "hold_days": payload.hold_days, "top_n": payload.top_n,
            "total_cost_bps": payload.total_cost_bps, "factors": payload.factors,
        }
        knowledge_cutoff = deps.as_utc(exchange_day_end(end))
        data_manifest_id = latest_data_manifest_id(connection, end, knowledge_cutoff)
        research_run_id = start_research_run(
            connection,
            experiment_type="strategy_backtest",
            strategy_key=payload.strategy_key,
            strategy_version=str(registry.get("version") or "unknown"),
            universe_key=payload.universe_key,
            start_date=start,
            end_date=end,
            knowledge_cutoff=knowledge_cutoff,
            parameters=parameters,
            data_manifest_id=data_manifest_id,
            input_datasets=STRATEGY_INPUT_DATASETS,
            data_schema_version="strategy-backtest-v1",
            json_value=deps.json_value,
        )
        try:
            result = deps.run_multi_factor_strategy(connection, payload.universe_key, start, end, parameters)
        except ValueError as error:
            finish_research_run(connection, research_run_id, status="failed", error_message=str(error)[:1000], json_value=deps.json_value)
            pending_error = deps.http_exception(status_code=422, detail=str(error))
            result = {}
        if pending_error is None:
            row = connection.execute(
                """INSERT INTO quant.strategy_experiments(strategy_key,universe_key,start_date,end_date,status,parameters,metrics,equity_curve,trades,research_run_id)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING strategy_experiment_id""",
                (payload.strategy_key, payload.universe_key, start, end, result["status"], deps.json_value(result["parameters"]),
                 deps.json_value(result["metrics"]), deps.json_value(result["equity_curve"]), deps.json_value(result["trades"]), research_run_id),
            ).fetchone()
            result["strategy_experiment_id"] = str(row["strategy_experiment_id"])
            result["research_run_id"] = str(research_run_id)
            result["data_manifest_id"] = data_manifest_id
            result["output_digest"] = finish_research_run(
                connection, research_run_id, status="completed", output=result, json_value=deps.json_value,
            )
    if pending_error is not None:
        raise pending_error
    return result


def build_snapshot(payload: Any, deps: ResearchExperimentDependencies) -> dict[str, Any]:
    """Persist a data-quality manifest; a blocked manifest is never "ready"."""
    as_of = payload.as_of_date or deps.china_today()
    requested_cutoff = payload.knowledge_cutoff or exchange_day_end(as_of)
    cutoff = deps.as_utc(availability_cutoff(as_of, requested_cutoff))
    with deps.database.transaction() as connection:
        manifest_row = connection.execute(
            """SELECT (SELECT count(*)::int FROM quant.canonical_bars_daily WHERE trading_date<=%s) bars,
                      (SELECT count(*)::int FROM quant.remote_reports WHERE remote_updated_at<=%s) remote_reports,
                      (SELECT count(*)::int FROM quant.canonical_bars_daily WHERE symbol='000300.SH' AND trading_date<=%s) benchmark_bars,
                      (SELECT count(DISTINCT symbol)::int FROM quant.canonical_bars_daily
                        WHERE trading_date=%s
                          AND symbol ~ '^(?:(?:60[0135]|68[0-9])[0-9]{3}\\.SH|(?:000|001|002|003|300|301|302)[0-9]{3}\\.SZ|[489][0-9]{5}\\.BJ)$') equity_symbols,
                      (SELECT count(DISTINCT basic.symbol)::int
                         FROM quant.canonical_bars_daily bar
                         JOIN quant.daily_fundamentals basic
                           ON basic.symbol=bar.symbol AND basic.trading_date=bar.trading_date
                        WHERE bar.trading_date=%s
                          AND bar.symbol ~ '^(?:(?:60[0135]|68[0-9])[0-9]{3}\\.SH|(?:000|001|002|003|300|301|302)[0-9]{3}\\.SZ|[489][0-9]{5}\\.BJ)$') fundamental_symbols,
                      (SELECT count(DISTINCT limits.symbol)::int
                         FROM quant.canonical_bars_daily bar
                         JOIN quant.daily_trade_limits limits
                           ON limits.symbol=bar.symbol AND limits.trading_date=bar.trading_date
                        WHERE bar.trading_date=%s
                          AND bar.symbol ~ '^(?:(?:60[0135]|68[0-9])[0-9]{3}\\.SH|(?:000|001|002|003|300|301|302)[0-9]{3}\\.SZ|[489][0-9]{5}\\.BJ)$') limit_symbols,
                      (SELECT is_open FROM quant.market_trade_calendar WHERE exchange='SSE' AND calendar_date=%s) exchange_open,
                      (SELECT count(*)::int FROM quant.data_quality_issues WHERE resolved_at IS NULL AND severity IN ('error','blocking')) blocking_issues""",
            (as_of, cutoff, as_of, as_of, as_of, as_of, as_of),
        ).fetchone()
        manifest = dict(manifest_row or {})
        manifest.update({
            "manifest_version": MANIFEST_VERSION,
            "as_of_date": as_of.isoformat(),
            "knowledge_cutoff": cutoff.isoformat(),
            "code_sha": os.environ.get("APP_GIT_SHA") or "unknown",
            "data_schema_version": "feature-availability-cutoff-v1",
        })
        complete_equity_controls = (
            manifest["equity_symbols"] > 0
            and manifest["fundamental_symbols"] >= manifest["equity_symbols"]
            and manifest["limit_symbols"] >= manifest["equity_symbols"]
        )
        status = "ready" if not manifest["blocking_issues"] and manifest["benchmark_bars"] and manifest["exchange_open"] and complete_equity_controls else "blocked"
        content_sha256 = manifest_digest(manifest)
        snapshot_key = research_snapshot_key(as_of, cutoff, content_sha256)
        connection.execute(
            """INSERT INTO quant.data_snapshots(snapshot_key,as_of_date,knowledge_cutoff,status,manifest_version,code_sha,data_schema_version,manifest,content_sha256,finalized_at)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s='ready' THEN now() ELSE null END)
               ON CONFLICT(snapshot_key) DO NOTHING""",
            (snapshot_key, as_of, cutoff, status, MANIFEST_VERSION, manifest["code_sha"], manifest["data_schema_version"],
             deps.json_value(manifest), content_sha256, status),
        )
    return {"snapshot_key": snapshot_key, "as_of_date": str(as_of), "knowledge_cutoff": cutoff,
            "status": status, "manifest_sha256": content_sha256, "manifest": manifest}


__all__ = [
    "FACTOR_INPUT_DATASETS", "STRATEGY_INPUT_DATASETS", "ResearchExperimentDependencies",
    "backtest_strategy", "build_snapshot", "evaluate_factors", "latest_data_manifest_id", "research_window",
]
