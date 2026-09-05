"""Bounded, read-only projections for research catalog and experiment results."""

from __future__ import annotations

from typing import Any


def _limit(value: int, maximum: int) -> int:
    return max(1, min(int(value), maximum))


def universe_members(database: Any, universe_key: str) -> dict[str, Any]:
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT m.universe_key,m.symbol,m.enabled,m.priority,m.source,m.metadata,m.added_at,m.updated_at,
                      i.name,i.industry,i.is_st FROM quant.universe_members m JOIN quant.instruments i ON i.symbol=m.symbol
               WHERE m.universe_key=%s ORDER BY m.priority,m.symbol""",
            (universe_key,),
        ).fetchall()
    return {"universe_key": universe_key, "items": rows}


def latest_features(database: Any, universe_key: str, limit: int) -> dict[str, Any]:
    with database.transaction() as connection:
        snapshot = connection.execute(
            """SELECT f.snapshot_key,f.as_of_date,f.feature_version,f.knowledge_cutoff,max(f.created_at) created_at
               FROM quant.feature_snapshots f JOIN quant.universe_members m ON m.symbol=f.symbol
               WHERE m.universe_key=%s GROUP BY f.snapshot_key,f.as_of_date,f.feature_version,f.knowledge_cutoff
               ORDER BY created_at DESC LIMIT 1""",
            (universe_key,),
        ).fetchone()
        if not snapshot:
            return {"snapshot": None, "items": []}
        rows = connection.execute(
            """SELECT f.symbol,i.name,f.features,f.quality_flags FROM quant.feature_snapshots f JOIN quant.instruments i ON i.symbol=f.symbol
               WHERE f.snapshot_key=%s AND f.feature_version=%s ORDER BY f.symbol LIMIT %s""",
            (snapshot["snapshot_key"], snapshot["feature_version"], max(1, min(limit, 1000))),
        ).fetchall()
    return {"snapshot": snapshot, "items": rows}


def factor_registry(database: Any) -> dict[str, Any]:
    with database.transaction() as connection:
        rows = connection.execute(
            "SELECT factor_key,label,category,implementation,inputs,formula,framework_tags,version,status,metadata,updated_at FROM quant.factor_registry ORDER BY category,factor_key"
        ).fetchall()
    return {"items": rows}


def factor_evaluations(database: Any, universe_key: str, limit: int) -> dict[str, Any]:
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT e.evaluation_id,e.factor_key,f.label,e.universe_key,e.start_date,e.end_date,e.horizon_days,e.engine,e.status,
                      e.observations,e.cross_section_days,e.metrics,e.artifact,e.created_at
               FROM quant.factor_evaluations e JOIN quant.factor_registry f ON f.factor_key=e.factor_key
               WHERE e.universe_key=%s ORDER BY e.created_at DESC,e.factor_key LIMIT %s""",
            (universe_key, max(1, min(limit, 500))),
        ).fetchall()
    return {"items": rows}


def strategy_registry(database: Any) -> dict[str, Any]:
    with database.transaction() as connection:
        rows = connection.execute(
            "SELECT strategy_key,label,engine,version,configuration,status,updated_at FROM quant.strategy_registry ORDER BY strategy_key"
        ).fetchall()
    return {"items": rows}


def strategy_experiments(database: Any, universe_key: str, limit: int) -> dict[str, Any]:
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT e.strategy_experiment_id,e.strategy_key,s.label,e.universe_key,e.start_date,e.end_date,e.status,e.parameters,
                      e.metrics,e.equity_curve,e.trades,e.created_at
               FROM quant.strategy_experiments e JOIN quant.strategy_registry s ON s.strategy_key=e.strategy_key
               WHERE e.universe_key=%s ORDER BY e.created_at DESC LIMIT %s""",
            (universe_key, max(1, min(limit, 200))),
        ).fetchall()
    return {"items": rows}


def data_quality_issues(database: Any, limit: int) -> dict[str, Any]:
    with database.transaction() as connection:
        rows = connection.execute(
            "SELECT * FROM quant.data_quality_issues WHERE resolved_at IS NULL ORDER BY created_at DESC LIMIT %s",
            (max(1, min(limit, 500)),),
        ).fetchall()
    return {"items": rows}


def research_runs(
    database: Any,
    experiment_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List reproducible research runs without exposing any write boundary."""
    conditions: list[str] = []
    values: list[Any] = []
    if experiment_type:
        conditions.append("experiment_type=%s")
        values.append(experiment_type)
    if status:
        conditions.append("status=%s")
        values.append(status)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    values.append(_limit(limit, 200))
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT research_run_id,experiment_type,strategy_key,strategy_version,universe_key,
                      start_date,end_date,knowledge_cutoff,data_manifest_id,code_sha,data_schema_version,
                      parameters,status,output_digest,error_message,started_at,finished_at,created_at
                 FROM quant.research_experiment_runs"""
            + where + " ORDER BY started_at DESC LIMIT %s",
            tuple(values),
        ).fetchall()
    return {"items": rows, "research_only": True, "live_effect": "none"}


def research_run(database: Any, research_run_id: Any) -> dict[str, Any]:
    """Return one run and its input/output lineage edges."""
    with database.transaction() as connection:
        run = connection.execute(
            """SELECT research_run_id,experiment_type,strategy_key,strategy_version,universe_key,
                      start_date,end_date,knowledge_cutoff,data_manifest_id,code_sha,data_schema_version,
                      parameters,status,output_digest,error_message,started_at,finished_at,created_at
                 FROM quant.research_experiment_runs WHERE research_run_id=%s""",
            (research_run_id,),
        ).fetchone()
        if not run:
            return {"run": None, "lineage": [], "research_only": True, "live_effect": "none"}
        lineage = connection.execute(
            """SELECT lineage_id,research_run_id,direction,dataset_key,dataset_version,
                      content_sha256,metadata,created_at
                 FROM quant.research_lineage_edges
                WHERE research_run_id=%s ORDER BY direction,dataset_key,created_at""",
            (research_run_id,),
        ).fetchall()
    return {"run": run, "lineage": lineage, "research_only": True, "live_effect": "none"}


__all__ = [
    "data_quality_issues", "factor_evaluations", "factor_registry", "latest_features",
    "research_run", "research_runs", "strategy_experiments", "strategy_registry", "universe_members",
]
