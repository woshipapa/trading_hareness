"""One-shot, resource-bounded worker for a real daily research baseline.

The worker reads only persisted point-in-time daily evidence, exports the exact
training table as an immutable artifact, runs several deterministic embargoed
OOF trials, and appends audit rows.  It has no provider client, scheduler,
model-loading path, threshold write, or promotion capability.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from psycopg.types.json import Json

from .database import Database
from .owner_storage import eligible_cold_tables, tiered_relation_sql
from .research_feature_export import export_training_table
from .research_manifest import canonical_json, manifest_digest
from .research_model_registry import admission
from .research_model_training import TRAINING_VERSION, train_oof


MODEL_KEY = "daily_pit_logistic_baseline"
MODEL_FAMILY = "daily_cross_section_binary"
FEATURE_CONTRACT_VERSION = "daily-pit-price-fundamental-sample-v1"
LABEL_CONTRACT_VERSION = "adjusted-close-positive-5-session-v1"
FEATURE_NAMES = (
    "momentum_5d",
    "momentum_20d",
    "sma_gap_20d",
    "volatility_20d",
    "volume_ratio_20d",
    "intraday_strength",
    "log_market_cap",
)
DEFAULT_TRIALS = (
    {"trial_key": "l2_0p001", "l2": 0.001, "learning_rate": 0.10, "iterations": 200},
    {"trial_key": "l2_0p01", "l2": 0.01, "learning_rate": 0.10, "iterations": 200},
    {"trial_key": "l2_0p1", "l2": 0.1, "learning_rate": 0.10, "iterations": 200},
)
MIN_FULL_CROSS_SECTION_DAYS = 720
# The exporter intentionally uses a deterministic audit sample.  A 256-symbol
# export exceeded the worker's hard 4 GiB limit in a real run; 64 produced
# 42k+ observations over 703 sessions and remains large enough for the formal
# sample gates while keeping a repeatable resource ceiling.
DEFAULT_SAMPLE_SYMBOLS = 64
LABEL_HORIZON_DAYS = 5


TRAINING_ROWS_SQL = """
WITH calendar AS (
    SELECT calendar_date AS trading_date,
           row_number() OVER (ORDER BY calendar_date)::int AS trading_index
      FROM quant.market_trade_calendar
     WHERE exchange='SSE' AND is_open
), candidate_symbols AS (
    SELECT symbol
      FROM (
          SELECT DISTINCT membership.symbol,
                 md5(membership.symbol || ':' || %s) AS sample_order
            FROM quant.universe_membership_history membership
           WHERE membership.universe_key=%s
             AND membership.effective_from<=%s
             AND coalesce(membership.effective_to,%s)>= %s
      ) candidates
     ORDER BY sample_order,symbol
     LIMIT %s
), source AS (
    SELECT bar.symbol,bar.trading_date,calendar.trading_index,
           (bar.open*adjustment.adj_factor)::double precision AS adjusted_open,
           (bar.high*adjustment.adj_factor)::double precision AS adjusted_high,
           (bar.low*adjustment.adj_factor)::double precision AS adjusted_low,
           (bar.close*adjustment.adj_factor)::double precision AS adjusted_close,
           bar.volume::double precision AS volume,
           bar.is_suspended,
           greatest(bar.available_at,adjustment.available_at,fundamental.available_at) AS input_available_at,
           fundamental.total_mv::double precision AS total_mv
      FROM quant.canonical_bars_daily bar
      JOIN candidate_symbols sampled USING(symbol)
      JOIN calendar USING(trading_date)
      JOIN quant.universe_membership_history membership
        ON membership.universe_key=%s AND membership.symbol=bar.symbol
       AND membership.effective_from<=bar.trading_date
       AND (membership.effective_to IS NULL OR membership.effective_to>=bar.trading_date)
      JOIN LATERAL (
          SELECT item.adj_factor,item.available_at
            FROM quant.daily_adjustment_factors item
           WHERE item.symbol=bar.symbol AND item.trading_date=bar.trading_date
             AND item.provider IN ('tushare','tushare_primary','tushare_super_get','tushare_super_sdk','tushare_super','tushare_backup','longhu_qfq_derived')
             AND ((item.raw->>'factor_semantics') IN ('corporate_action_cumulative','cumulative_tushare','cumulative','longhu_qfq_derived')
                  OR (item.provider='longhu_qfq_derived' AND item.raw->>'method'='longhu_cq_preclose_qfq_v2'))
             AND item.available_at < ((bar.trading_date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
           ORDER BY CASE WHEN item.provider='longhu_qfq_derived' THEN 0
                         WHEN item.provider IN ('tushare_primary','tushare_super_sdk') THEN 1 ELSE 2 END,
                    item.available_at DESC,
                    item.provider
           LIMIT 1
      ) adjustment ON TRUE
      JOIN LATERAL (
          SELECT item.total_mv,item.available_at
            FROM quant.daily_fundamentals item
           WHERE item.symbol=bar.symbol AND item.trading_date=bar.trading_date
             AND item.available_at < ((bar.trading_date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
           ORDER BY item.available_at DESC,
                    CASE WHEN item.provider='longhu_qfq_derived' THEN 0
                         WHEN item.provider IN ('tushare_primary','tushare_super_sdk') THEN 1 ELSE 2 END,
                    item.provider
           LIMIT 1
      ) fundamental ON TRUE
     WHERE bar.quality_status='fresh' AND bar.trading_date<=%s
), lagged AS (
    SELECT source.*,
           lag(adjusted_close,1) OVER symbol_time AS close_1d,
           lag(adjusted_close,5) OVER symbol_time AS close_5d,
           lag(adjusted_close,20) OVER symbol_time AS close_20d,
           lag(trading_index,20) OVER symbol_time AS index_20d,
           avg(adjusted_close) OVER symbol_20 AS sma_20d,
           avg(volume) OVER symbol_20_prior AS volume_20d,
           max(input_available_at) OVER symbol_20 AS feature_available_at
      FROM source
    WINDOW symbol_time AS (PARTITION BY symbol ORDER BY trading_index),
           symbol_20 AS (PARTITION BY symbol ORDER BY trading_index ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
           symbol_20_prior AS (PARTITION BY symbol ORDER BY trading_index ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING)
), returns AS (
    SELECT lagged.*,
           ln(adjusted_close/nullif(close_1d,0)) AS log_return
      FROM lagged
), featured AS (
    SELECT returns.*,
           stddev_samp(log_return) OVER (
               PARTITION BY symbol ORDER BY trading_index ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
           ) AS volatility_20d
      FROM returns
), labelled AS (
    SELECT current.symbol,current.trading_date AS exchange_date,
           current.adjusted_close/nullif(current.close_5d,0)-1 AS momentum_5d,
           current.adjusted_close/nullif(current.close_20d,0)-1 AS momentum_20d,
           current.adjusted_close/nullif(current.sma_20d,0)-1 AS sma_gap_20d,
           current.volatility_20d,
           current.volume/nullif(current.volume_20d,0) AS volume_ratio_20d,
           (current.adjusted_close-current.adjusted_open)/
               nullif(current.adjusted_high-current.adjusted_low,0) AS intraday_strength,
           ln(current.total_mv) AS log_market_cap,
           CASE WHEN future.adjusted_close>current.adjusted_close THEN 1 ELSE 0 END::int AS label,
           current.feature_available_at,
           future.input_available_at AS label_available_at
      FROM featured current
      JOIN source future
        ON future.symbol=current.symbol
       AND future.trading_index=current.trading_index+%s
     WHERE current.trading_date BETWEEN %s AND %s
       AND current.index_20d=current.trading_index-20
       AND NOT current.is_suspended AND NOT future.is_suspended
)
SELECT symbol,exchange_date,label,feature_available_at,label_available_at,
       momentum_5d,momentum_20d,sma_gap_20d,volatility_20d,
       volume_ratio_20d,intraday_strength,log_market_cap
  FROM labelled
 WHERE momentum_5d IS NOT NULL AND momentum_20d IS NOT NULL
   AND sma_gap_20d IS NOT NULL AND volatility_20d IS NOT NULL
   AND volume_ratio_20d IS NOT NULL AND intraday_strength IS NOT NULL
   AND log_market_cap IS NOT NULL
   AND feature_available_at<=label_available_at
 ORDER BY exchange_date,symbol
"""


def build_training_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Convert SQL evidence rows to the framework-neutral export contract."""
    return [
        {
            "symbol": str(row["symbol"]),
            "exchange_date": row["exchange_date"],
            "label": int(row["label"]),
            "feature_available_at": row["feature_available_at"],
            "label_available_at": row["label_available_at"],
            "features": {name: float(row[name]) for name in FEATURE_NAMES},
        }
        for row in rows
    ]


def _training_input(export: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "exchange_date": row["exchange_date"],
            "label": row["label"],
            **{name: row["features"][name] for name in FEATURE_NAMES},
        }
        for row in export["rows"]
    ]


def _write_artifact(directory: Path, name: str, payload: bytes) -> tuple[Path, str]:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    temporary = directory / f".{name}.tmp-{os.getpid()}"
    temporary.write_bytes(payload)
    os.replace(temporary, path)
    return path, hashlib.sha256(payload).hexdigest()


def _json_safe(value: Any) -> Any:
    """Round-trip database dates/decimals through the canonical encoder."""
    return json.loads(canonical_json(value))


def _code_identity() -> dict[str, Any]:
    """Bind the small executable training surface even in a dirty workspace."""
    directory = Path(__file__).resolve().parent
    names = (
        "research_feature_export.py",
        "research_model_training.py",
        "research_model_training_worker.py",
        "strategy_validation.py",
    )
    files = {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
        for name in names
    }
    return {
        "source_sha256": manifest_digest(files),
        "files": files,
        "image_revision": os.getenv("APP_GIT_SHA") or "unknown",
        "image_release": os.getenv("APP_RELEASE") or "unknown",
    }


def _coverage(connection: Any, start_date: date, end_date: date) -> dict[str, Any]:
    return dict(connection.execute(
        """SELECT count(*) FILTER(WHERE is_full_cross_section)::int AS full_days,
                  min(trading_date) FILTER(WHERE is_full_cross_section) AS first_full_date,
                  max(trading_date) FILTER(WHERE is_full_cross_section) AS last_full_date
             FROM quant.replay_readiness_daily_coverage
            WHERE trading_date BETWEEN %s AND %s""",
        (start_date, end_date),
    ).fetchone() or {})


def _training_sql(connection: Any) -> str:
    """Use owner cold twins only after the complete atomic cutover passes."""
    cold_tables = eligible_cold_tables(connection) if hasattr(connection, "cursor") else set()
    sql = TRAINING_ROWS_SQL
    for logical_name in ("canonical_bars_daily", "daily_fundamentals", "daily_adjustment_factors"):
        sql = sql.replace(f"quant.{logical_name}", tiered_relation_sql(logical_name, cold_tables))
    return sql


def _register(
    connection: Any,
    *,
    model_version: str,
    artifact_uri: str,
    artifact_sha256: str,
    export: Mapping[str, Any],
    selected: Mapping[str, Any],
    trials: Sequence[Mapping[str, Any]],
    metadata: Mapping[str, Any],
    registry_status: str,
) -> str:
    row = connection.execute(
        """INSERT INTO quant.research_model_registry(
               model_key,model_family,model_version,framework,artifact_uri,artifact_sha256,
               data_snapshot_key,feature_contract_version,label_contract_version,status,
               trial_count,independent_days,sample_count,metrics,metadata)
           VALUES(%s,%s,%s,'numpy-logistic',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT(model_key,model_version) DO NOTHING
           RETURNING model_id""",
        (
            MODEL_KEY, MODEL_FAMILY, model_version, artifact_uri, artifact_sha256,
            export["data_snapshot_key"], FEATURE_CONTRACT_VERSION, LABEL_CONTRACT_VERSION,
            registry_status,
            len(trials), selected["independent_days"], selected["samples"],
            Json(_json_safe(selected["metrics"])), Json(_json_safe(dict(metadata))),
        ),
    ).fetchone()
    if row:
        model_id = str(row["model_id"])
    else:
        existing = connection.execute(
            """SELECT model_id,artifact_sha256,data_snapshot_key
                 FROM quant.research_model_registry
                WHERE model_key=%s AND model_version=%s""",
            (MODEL_KEY, model_version),
        ).fetchone()
        if not existing or existing["artifact_sha256"] != artifact_sha256 or existing["data_snapshot_key"] != export["data_snapshot_key"]:
            raise RuntimeError("model version already exists with different immutable lineage")
        model_id = str(existing["model_id"])
    for trial in trials:
        trial_artifact_sha = manifest_digest({
            "parameters": trial["parameters"], "metrics": trial["metrics"], "artifact": trial["artifact"],
        })
        connection.execute(
            """INSERT INTO quant.research_model_trials(
                   model_id,trial_key,training_version,status,parameters,metrics,
                   independent_days,sample_count,artifact_sha256,metadata,live_effect)
               VALUES(%s,%s,%s,'completed',%s,%s,%s,%s,%s,%s,'none')
               ON CONFLICT(model_id,trial_key) DO NOTHING""",
            (
                model_id, trial["trial_key"], TRAINING_VERSION,
                Json(_json_safe(trial["parameters"])), Json(_json_safe(trial["metrics"])),
                trial["independent_days"], trial["samples"], trial_artifact_sha,
                Json({"research_only": True, "replay_only": True, "selected": trial["trial_key"] == selected["trial_key"]}),
            ),
        )
    return model_id


def run_training_job(
    database: Any,
    *,
    start_date: date,
    end_date: date,
    universe_key: str = "all_a",
    sample_symbols: int = DEFAULT_SAMPLE_SYMBOLS,
    artifact_root: Path | None = None,
    trials: Sequence[Mapping[str, Any]] = DEFAULT_TRIALS,
    required_full_cross_section_days: int = MIN_FULL_CROSS_SECTION_DAYS,
) -> dict[str, Any]:
    """Run and register a daily-only research model; never validate/promote it."""
    if start_date >= end_date:
        raise ValueError("start_date must be before end_date")
    if sample_symbols < 1 or sample_symbols > 1024:
        raise ValueError("sample_symbols must be between 1 and 1024")
    if not trials:
        raise ValueError("at least one pre-registered trial is required")
    with database.transaction() as connection:
        coverage = _coverage(connection, start_date, end_date)
        if int(coverage.get("full_days") or 0) < required_full_cross_section_days:
            return {
                "status": "blocked", "blockers": ["less_than_required_full_cross_section_days"],
                "coverage": coverage, "required_full_cross_section_days": required_full_cross_section_days,
                "research_only": True, "replay_only": True, "live_effect": "none",
            }
        result = connection.execute(
            _training_sql(connection),
            (
                FEATURE_CONTRACT_VERSION, universe_key, end_date, end_date, start_date,
                sample_symbols, universe_key, end_date, LABEL_HORIZON_DAYS, start_date, end_date,
            ),
        )
        source_rows = result.fetchall()
    exported = export_training_table(
        build_training_rows(source_rows),
        feature_names=FEATURE_NAMES,
        feature_contract_version=FEATURE_CONTRACT_VERSION,
        label_contract_version=LABEL_CONTRACT_VERSION,
    )
    if exported["status"] != "exported_research_only":
        return {**exported, "coverage": coverage}
    training_rows = _training_input(exported)
    completed_trials: list[dict[str, Any]] = []
    for specification in trials:
        trial_key = str(specification["trial_key"])
        result = train_oof(
            training_rows,
            feature_names=FEATURE_NAMES,
            l2=float(specification["l2"]),
            learning_rate=float(specification["learning_rate"]),
            iterations=int(specification["iterations"]),
            include_predictions=False,
        )
        if result["status"] != "trained_research_only":
            return {
                "status": "blocked", "blockers": [f"trial_blocked:{trial_key}", *result.get("blockers", [])],
                "coverage": coverage, "research_only": True, "replay_only": True, "live_effect": "none",
            }
        completed_trials.append({"trial_key": trial_key, **result})
    selected = min(
        completed_trials,
        key=lambda item: (float(item["metrics"]["log_loss"]), float(item["metrics"]["brier"]), item["trial_key"]),
    )
    model_version = f"{TRAINING_VERSION}-{exported['manifest_digest'][:12]}"
    root = artifact_root or Path(os.getenv("QUANT_DATA_DIR", "/var/lib/quant")) / "research-model-artifacts"
    directory = root / model_version
    dataset_payload = gzip.compress(exported["canonical_manifest"].encode("utf-8"), mtime=0)
    dataset_path, dataset_sha = _write_artifact(directory, "training-table.json.gz", dataset_payload)
    compact_trials = [
        {
            "trial_key": item["trial_key"], "version": item["version"], "parameters": item["parameters"],
            "independent_days": item["independent_days"], "samples": item["samples"],
            "oof_days": item["oof_days"], "oof_samples": item["oof_samples"],
            "folds": item["folds"], "metrics": item["metrics"], "artifact": item["artifact"],
        }
        for item in completed_trials
    ]
    code_identity = _code_identity()
    model_payload = {
        "model_key": MODEL_KEY, "model_version": model_version,
        "framework": "numpy-logistic", "training_version": TRAINING_VERSION,
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
        "label_contract_version": LABEL_CONTRACT_VERSION,
        "data_snapshot_key": exported["data_snapshot_key"],
        "dataset_sha256": dataset_sha,
        "code_identity": code_identity,
        "selected_trial_key": selected["trial_key"],
        "trials": compact_trials,
        "research_only": True, "replay_only": True, "live_effect": "none",
        "policy": "A trained audit artifact is not validated, promoted, or loaded by the online service.",
    }
    model_bytes = canonical_json(model_payload).encode("utf-8")
    model_path, artifact_sha = _write_artifact(directory, "model.json", model_bytes)
    manifest_payload = {
        "model_sha256": artifact_sha, "dataset_sha256": dataset_sha,
        "model_file": model_path.name, "dataset_file": dataset_path.name,
        "data_snapshot_key": exported["data_snapshot_key"],
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
        "label_contract_version": LABEL_CONTRACT_VERSION,
        "code_identity": code_identity,
        "samples": exported["samples"], "independent_days": exported["independent_days"],
        "research_only": True, "live_effect": "none",
    }
    _write_artifact(directory, "manifest.json", canonical_json(manifest_payload).encode("utf-8"))
    registry_record = {
        "model_key": MODEL_KEY, "model_version": model_version,
        "artifact_sha256": artifact_sha, "data_snapshot_key": exported["data_snapshot_key"],
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
        "label_contract_version": LABEL_CONTRACT_VERSION,
        "independent_days": selected["independent_days"], "sample_count": selected["samples"],
        "metrics": selected["metrics"],
    }
    performance_blockers = [
        blocker for blocker, blocked in (
            ("does_not_beat_constant_log_loss", not bool(selected["metrics"].get("beats_constant_log_loss"))),
            ("roc_auc_not_above_random", (selected["metrics"].get("roc_auc") or 0) <= 0.5),
        ) if blocked
    ]
    registry_status = "rejected" if performance_blockers else "trained"
    metadata = {
        "admission": admission(registry_record),
        "performance_gate": {
            "status": "rejected" if performance_blockers else "candidate_only",
            "blockers": performance_blockers,
            "requires": ["beats_constant_log_loss", "roc_auc_above_0.5"],
            "notice": "Passing this weak baseline is necessary but never sufficient for validation or promotion.",
        },
        "selected_trial_key": selected["trial_key"], "dataset_sha256": dataset_sha,
        "code_identity": code_identity,
        "coverage": coverage, "sample_symbols": sample_symbols, "universe_key": universe_key,
        "start_date": str(start_date), "end_date": str(end_date),
        "daily_only": True, "intraday_validation": "not_performed",
        "promotion_policy": "manual review still cannot bypass separate data and validation gates",
        "research_only": True, "replay_only": True, "live_effect": "none",
    }
    with database.transaction() as connection:
        model_id = _register(
            connection,
            model_version=model_version,
            artifact_uri=f"quant-artifact://research-model-artifacts/{model_version}/model.json",
            artifact_sha256=artifact_sha,
            export=exported,
            selected=selected,
            trials=completed_trials,
            metadata=metadata,
            registry_status=registry_status,
        )
    return {
        "status": "trained_research_only", "model_id": model_id,
        "model_key": MODEL_KEY, "model_version": model_version,
        "selected_trial_key": selected["trial_key"], "trial_count": len(completed_trials),
        "samples": selected["samples"], "independent_days": selected["independent_days"],
        "metrics": selected["metrics"], "artifact_sha256": artifact_sha,
        "data_snapshot_key": exported["data_snapshot_key"], "coverage": coverage,
        "registry_status": registry_status, "performance_blockers": performance_blockers,
        "research_only": True, "replay_only": True, "live_effect": "none",
    }


def _date_value(value: str) -> date:
    return date.fromisoformat(value)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the daily PIT research-only model worker")
    parser.add_argument("--start-date", type=_date_value)
    parser.add_argument("--end-date", type=_date_value)
    parser.add_argument("--universe-key", default="all_a")
    parser.add_argument("--sample-symbols", type=int, default=DEFAULT_SAMPLE_SYMBOLS)
    parser.add_argument("--artifact-root", type=Path)
    arguments = parser.parse_args(argv)
    database = Database()
    try:
        with database.transaction() as connection:
            bounds = connection.execute(
                """SELECT min(trading_date) FILTER(WHERE is_full_cross_section) AS first_date,
                          max(trading_date) FILTER(WHERE is_full_cross_section) AS last_date
                     FROM quant.replay_readiness_daily_coverage"""
            ).fetchone() or {}
        start_date = arguments.start_date or bounds.get("first_date")
        end_date = arguments.end_date or bounds.get("last_date")
        if not start_date or not end_date:
            output = {"status": "blocked", "blockers": ["full_cross_section_bounds_unavailable"], "live_effect": "none"}
        else:
            output = run_training_job(
                database,
                start_date=start_date,
                end_date=end_date,
                universe_key=arguments.universe_key,
                sample_symbols=arguments.sample_symbols,
                artifact_root=arguments.artifact_root,
            )
        print(json.dumps(output, ensure_ascii=False, default=str, sort_keys=True))
        return 0 if output.get("status") == "trained_research_only" else 2
    finally:
        database.close()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_TRIALS", "FEATURE_NAMES", "LABEL_CONTRACT_VERSION", "MODEL_KEY",
    "TRAINING_ROWS_SQL", "_training_sql", "build_training_rows", "run_training_job",
]
