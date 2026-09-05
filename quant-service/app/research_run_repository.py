"""Append-only research experiment run and lineage persistence.

The ledger is deliberately small and database-driver agnostic.  It records
which data contracts a run consumed, the availability cutoff, and the digest of
its output.  It grants no strategy or execution permission.
"""

from __future__ import annotations

from datetime import date, datetime
import hashlib
import os
from collections.abc import Callable
from typing import Any, Iterable
from uuid import UUID, uuid4

from .research_manifest import manifest_digest


RUN_SCHEMA_VERSION = "research-run-v1"
_RUN_STATUSES = frozenset({"running", "completed", "blocked", "failed"})


def research_output_digest(output: Any) -> str:
    """Hash a JSON-compatible result using the shared manifest encoding."""

    return manifest_digest(output)


def _dataset_digest(direction: str, dataset_key: str, dataset_version: str) -> str:
    material = f"{direction}:{dataset_key}:{dataset_version}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("research run knowledge cutoff must be timezone-aware")
    return value


def start_research_run(
    connection: Any,
    *,
    experiment_type: str,
    universe_key: str | None,
    start_date: date | None,
    end_date: date | None,
    knowledge_cutoff: datetime,
    parameters: Any,
    input_datasets: Iterable[str],
    strategy_key: str | None = None,
    strategy_version: str | None = None,
    data_manifest_id: str | None = None,
    code_sha: str | None = None,
    data_schema_version: str = RUN_SCHEMA_VERSION,
    json_value: Callable[[Any], Any] | None = None,
) -> UUID:
    """Create one run and its immutable input lineage edges."""

    cutoff = _require_aware(knowledge_cutoff)
    run_id = uuid4()
    resolved_code_sha = code_sha or os.environ.get("APP_GIT_SHA") or "unknown"
    encode_json = json_value or (lambda value: value)
    connection.execute(
        """INSERT INTO quant.research_experiment_runs(
                   research_run_id,experiment_type,strategy_key,strategy_version,universe_key,
                   start_date,end_date,knowledge_cutoff,data_manifest_id,code_sha,
                   data_schema_version,parameters,status)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'running')""",
        (run_id, experiment_type, strategy_key, strategy_version, universe_key, start_date, end_date,
         cutoff, data_manifest_id, resolved_code_sha, data_schema_version, encode_json(parameters)),
    )
    for dataset_key in dict.fromkeys(str(item) for item in input_datasets):
        dataset_version = data_schema_version
        connection.execute(
            """INSERT INTO quant.research_lineage_edges(
                       research_run_id,direction,dataset_key,dataset_version,content_sha256,metadata)
                   VALUES(%s,'input',%s,%s,%s,%s)""",
            (run_id, dataset_key, dataset_version, _dataset_digest("input", dataset_key, dataset_version),
             encode_json({"manifest_id": data_manifest_id, "code_sha": resolved_code_sha})),
        )
    return run_id


def finish_research_run(
    connection: Any,
    run_id: UUID,
    *,
    status: str,
    output: Any | None = None,
    error_message: str | None = None,
    json_value: Callable[[Any], Any] | None = None,
) -> str | None:
    """Close a run and append one output lineage edge when a result exists."""

    if status not in _RUN_STATUSES - {"running"}:
        raise ValueError(f"invalid terminal research run status: {status}")
    digest = research_output_digest(output) if output is not None else None
    encode_json = json_value or (lambda value: value)
    connection.execute(
        """UPDATE quant.research_experiment_runs
              SET status=%s,output_digest=%s,error_message=%s,finished_at=now()
            WHERE research_run_id=%s AND status='running'""",
        (status, digest, error_message, run_id),
    )
    if digest is not None:
        connection.execute(
            """INSERT INTO quant.research_lineage_edges(
                       research_run_id,direction,dataset_key,dataset_version,content_sha256,metadata)
                   VALUES(%s,'output','research_result',%s,%s,%s)""",
            (run_id, RUN_SCHEMA_VERSION, digest, encode_json({"status": status})),
        )
    return digest


__all__ = ["RUN_SCHEMA_VERSION", "finish_research_run", "research_output_digest", "start_research_run"]
