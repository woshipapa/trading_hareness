"""Native async read projections for the research catalog."""

from __future__ import annotations

from typing import Any


def _limit(value: int, maximum: int) -> int:
    return max(1, min(value, maximum))


async def universe_members(async_database: Any, universe_key: str) -> dict[str, Any]:
    async with async_database.transaction() as conn:
        result = await conn.execute(
            """SELECT m.universe_key,m.symbol,m.enabled,m.priority,m.source,m.metadata,m.added_at,m.updated_at,
                      i.name,i.industry,i.is_st FROM quant.universe_members m JOIN quant.instruments i ON i.symbol=m.symbol
               WHERE m.universe_key=%s ORDER BY m.priority,m.symbol""", (universe_key,))
        rows = await result.fetchall()
    return {"universe_key": universe_key, "items": rows}


async def latest_features(async_database: Any, universe_key: str, limit: int) -> dict[str, Any]:
    async with async_database.transaction() as conn:
        result = await conn.execute(
            """SELECT f.snapshot_key,f.as_of_date,f.feature_version,f.knowledge_cutoff,max(f.created_at) created_at
               FROM quant.feature_snapshots f JOIN quant.universe_members m ON m.symbol=f.symbol
               WHERE m.universe_key=%s GROUP BY f.snapshot_key,f.as_of_date,f.feature_version,f.knowledge_cutoff
               ORDER BY created_at DESC LIMIT 1""", (universe_key,))
        snapshot = await result.fetchone()
        if not snapshot:
            return {"snapshot": None, "items": []}
        result = await conn.execute(
            """SELECT f.symbol,i.name,f.features,f.quality_flags FROM quant.feature_snapshots f JOIN quant.instruments i ON i.symbol=f.symbol
               WHERE f.snapshot_key=%s AND f.feature_version=%s ORDER BY f.symbol LIMIT %s""",
            (snapshot["snapshot_key"], snapshot["feature_version"], _limit(limit, 1000)))
        rows = await result.fetchall()
    return {"snapshot": snapshot, "items": rows}


async def factor_registry(async_database: Any) -> dict[str, Any]:
    async with async_database.transaction() as conn:
        result = await conn.execute("SELECT factor_key,label,category,implementation,inputs,formula,framework_tags,version,status,metadata,updated_at FROM quant.factor_registry ORDER BY category,factor_key")
        rows = await result.fetchall()
    return {"items": rows}


async def factor_evaluations(async_database: Any, universe_key: str, limit: int) -> dict[str, Any]:
    async with async_database.transaction() as conn:
        result = await conn.execute(
            """SELECT e.evaluation_id,e.research_run_id,e.factor_key,f.label,e.universe_key,e.start_date,e.end_date,e.horizon_days,e.engine,e.status,
                      e.observations,e.cross_section_days,e.metrics,e.artifact,e.created_at
               FROM quant.factor_evaluations e JOIN quant.factor_registry f ON f.factor_key=e.factor_key
               WHERE e.universe_key=%s ORDER BY e.created_at DESC,e.factor_key LIMIT %s""",
            (universe_key, _limit(limit, 500)))
        rows = await result.fetchall()
    return {"items": rows}


async def strategy_registry(async_database: Any) -> dict[str, Any]:
    async with async_database.transaction() as conn:
        result = await conn.execute("SELECT strategy_key,label,engine,version,configuration,status,updated_at FROM quant.strategy_registry ORDER BY strategy_key")
        rows = await result.fetchall()
    return {"items": rows}


async def strategy_experiments(async_database: Any, universe_key: str, limit: int) -> dict[str, Any]:
    async with async_database.transaction() as conn:
        result = await conn.execute(
            """SELECT e.strategy_experiment_id,e.research_run_id,e.strategy_key,s.label,e.universe_key,e.start_date,e.end_date,e.status,e.parameters,
                      e.metrics,e.equity_curve,e.trades,e.created_at
               FROM quant.strategy_experiments e JOIN quant.strategy_registry s ON s.strategy_key=e.strategy_key
               WHERE e.universe_key=%s ORDER BY e.created_at DESC LIMIT %s""",
            (universe_key, _limit(limit, 200)))
        rows = await result.fetchall()
    return {"items": rows}


async def data_quality_issues(async_database: Any, limit: int) -> dict[str, Any]:
    async with async_database.transaction() as conn:
        result = await conn.execute("SELECT * FROM quant.data_quality_issues WHERE resolved_at IS NULL ORDER BY created_at DESC LIMIT %s", (_limit(limit, 500),))
        rows = await result.fetchall()
    return {"items": rows}


async def latest_strategy_day_summary(async_database: Any, exchange_date: Any | None = None) -> dict[str, Any]:
    """Return the latest persisted daily learning receipt through the read pool."""
    where = " WHERE exchange_date=%s" if exchange_date is not None else ""
    params = (exchange_date,) if exchange_date is not None else ()
    async with async_database.transaction() as conn:
        result = await conn.execute(
            """SELECT exchange_date,payload,message_text,delivery_status,attempt_count,
                      sent_at,error_message,created_at,updated_at
                 FROM quant.strategy_day_summaries"""
            + where + " ORDER BY exchange_date DESC LIMIT 1",
            params,
        )
        row = await result.fetchone()
    return {"summary": row, "research_only": True, "live_effect": "none"}


async def research_runs(
    async_database: Any,
    experiment_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List reproducible research runs through the read-only async pool."""
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
    async with async_database.transaction() as conn:
        result = await conn.execute(
            """SELECT research_run_id,experiment_type,strategy_key,strategy_version,universe_key,
                      start_date,end_date,knowledge_cutoff,data_manifest_id,code_sha,data_schema_version,
                      parameters,status,output_digest,error_message,started_at,finished_at,created_at
                 FROM quant.research_experiment_runs"""
            + where + " ORDER BY started_at DESC LIMIT %s",
            tuple(values),
        )
        rows = await result.fetchall()
    return {"items": rows, "research_only": True, "live_effect": "none"}


async def research_run(async_database: Any, research_run_id: Any) -> dict[str, Any]:
    """Return one run and its input/output lineage edges."""
    async with async_database.transaction() as conn:
        result = await conn.execute(
            """SELECT research_run_id,experiment_type,strategy_key,strategy_version,universe_key,
                      start_date,end_date,knowledge_cutoff,data_manifest_id,code_sha,data_schema_version,
                      parameters,status,output_digest,error_message,started_at,finished_at,created_at
                 FROM quant.research_experiment_runs WHERE research_run_id=%s""",
            (research_run_id,),
        )
        run = await result.fetchone()
        if not run:
            return {"run": None, "lineage": [], "research_only": True, "live_effect": "none"}
        result = await conn.execute(
            """SELECT lineage_id,research_run_id,direction,dataset_key,dataset_version,
                      content_sha256,metadata,created_at
                 FROM quant.research_lineage_edges
                WHERE research_run_id=%s ORDER BY direction,dataset_key,created_at""",
            (research_run_id,),
        )
        lineage = await result.fetchall()
    return {"run": run, "lineage": lineage, "research_only": True, "live_effect": "none"}
