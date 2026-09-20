"""Read-only compatibility diagnostics for the owner's actual schema.

The peer never runs owner DDL. The owner production database is not required
to expose the peer's former proposed ``adjustment_state`` columns or five
market-data ``*_cold`` twins: its factor semantics live in ``raw`` JSON and
its operational cold layer contains evidence/audit tables. The process startup
gate uses the owner's machine-readable peer contract
(``app/owner_peer_contract.py``). ``owner_runtime_schema_status`` remains a
local read-only compatibility projection for health/diagnostics and checks
only the tables and columns the peer actually reads. The older hot/cold
classification helpers remain available for local evidence-tier experiments,
but are not a production startup prerequisite.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any


COLD_TABLESPACE = "stock_cold"
TIERED_EVIDENCE_TABLES = (
    "canonical_bars_daily",
    "daily_fundamentals",
    "daily_trade_limits",
    "daily_adjustment_factors",
    "security_suspensions",
)
LEGACY_COLD_RELATION = "legacy_source_records"
COMPLETE_FACTOR_PROVIDERS = (
    "tushare", "tushare_primary", "tushare_super_get", "tushare_super_sdk",
    "tushare_super", "tushare_backup", "longhu_qfq_derived",
)
SEMANTIC_REQUIRED_COLUMNS = {
    "canonical_bars_daily": ("adjustment_state",),
    "market_bars_daily": ("adjustment_state",),
    "daily_adjustment_factors": ("adjustment_state", "factor_semantics", "retired_at"),
}

# This is the owner 20260919_0106 contract observed on the production lane.
# Keep this list deliberately small: adding a column here is a compatibility
# change and must be justified by a concrete peer SQL read, not by a proposed
# handoff document or a local migration.
OWNER_RUNTIME_REQUIRED_COLUMNS = {
    "canonical_bars_daily": ("symbol", "trading_date", "adj_factor", "available_at", "quality_status"),
    "daily_adjustment_factors": ("symbol", "trading_date", "adj_factor", "provider", "available_at", "raw"),
}


def owner_runtime_schema_status(connection: Any) -> dict[str, Any]:
    """Return the minimal read-only schema contract used by peer processes.

    Factor rows are required to be real positive values; missing dates are
    represented by no row. Semantic interpretation is performed by the peer
    from ``daily_adjustment_factors.raw`` (including the owner's Longhu method)
    rather than by requiring owner-side columns that do not exist.
    """

    tables = tuple(OWNER_RUNTIME_REQUIRED_COLUMNS)
    try:
        rows = connection.execute(
            """SELECT table_name,column_name
                 FROM information_schema.columns
                WHERE table_schema='quant' AND table_name=ANY(%s)
                ORDER BY table_name,ordinal_position""",
            (list(tables),),
        ).fetchall()
        present: dict[str, set[str]] = {table: set() for table in tables}
        for row in rows:
            table = str(_row_value(row, "table_name", 0))
            column = str(_row_value(row, "column_name", 1))
            if table in present:
                present[table].add(column)
        missing = {
            table: [column for column in required if column not in present.get(table, set())]
            for table, required in OWNER_RUNTIME_REQUIRED_COLUMNS.items()
        }
        missing = {table: columns for table, columns in missing.items() if columns}
        lineage = _database_lineage(connection)
    except Exception as error:  # diagnostics must never become a write path
        return {
            "status": "incompatible",
            "schema_ready": False,
            "issues": [f"owner_runtime_schema_query_failed:{type(error).__name__}"],
            "database_lineage": {"status": "error", "database_name": None, "alembic_version": None},
        }
    issues = [
        f"{table}:missing:{column}"
        for table, columns in missing.items()
        for column in columns
    ]
    schema_ready = not issues and lineage.get("status") == "ready"
    return {
        "status": "owner_compatible" if schema_ready else "incompatible",
        "schema_ready": schema_ready,
        "issues": issues if issues else [],
        "required_columns": {table: list(columns) for table, columns in OWNER_RUNTIME_REQUIRED_COLUMNS.items()},
        "factor_semantics": {
            "storage": "daily_adjustment_factors.raw",
            "missing_factor_row": "absent",
            "adj_factor_nullable": False,
            "longhu_method": "longhu_cq_preclose_qfq_v2",
        },
        "cold_layer": {
            "policy": "owner_managed_evidence_only",
            "peer_dependency": False,
        },
        "database_lineage": lineage,
    }


def _semantic_guard(
    connection: Any,
    relation_tablespaces: Mapping[str, str | None],
    semantic_ready: bool,
) -> dict[str, Any]:
    """Read-only zero-violation guard for rows marked research-complete."""
    if not semantic_ready:
        return {"status": "not_evaluated", "invalid_relations": [], "reason": "semantic_columns_missing"}
    relations = []
    for logical_name in ("canonical_bars_daily", "market_bars_daily", "daily_adjustment_factors"):
        for relation in (logical_name, f"{logical_name}_cold"):
            if relation in relation_tablespaces:
                relations.append((logical_name, relation))
    if not relations:
        return {"status": "error", "invalid_relations": [], "reason": "semantic_relations_missing"}
    provider_sql = ",".join("%s" for _ in COMPLETE_FACTOR_PROVIDERS)
    expressions = []
    for logical_name, relation in relations:
        if logical_name == "daily_adjustment_factors":
            condition = (
                "adjustment_state='complete' AND "
                "(factor_semantics IS DISTINCT FROM 'cumulative_tushare' OR adj_factor IS NULL OR adj_factor<=0 "
                f"OR provider IS NULL OR provider NOT IN ({provider_sql}))"
            )
        else:
            # A bar's ``source``/``selected_provider`` identifies the price
            # observation, not the provider of its corporate-action factor.
            # Owner maintenance may legitimately pair an AkShare/Tencent bar
            # with a separately verified Tushare factor.  Do not correlate
            # millions of bars with the factor table in a health probe; the
            # factor relations below independently enforce the licensed
            # provider and semantic contract.
            condition = "adjustment_state='complete' AND (adj_factor IS NULL OR adj_factor<=0)"
        expressions.append(
            f"EXISTS (SELECT 1 FROM quant.{relation} WHERE {condition}) AS \"{relation}\""
        )
    parameters = tuple(
        provider for logical_name, _relation in relations if logical_name == "daily_adjustment_factors"
        for provider in COMPLETE_FACTOR_PROVIDERS
    )
    try:
        row = connection.execute("SELECT " + ",".join(expressions), parameters).fetchone()
    except Exception as error:  # fail closed without turning health into a write path
        return {"status": "error", "invalid_relations": [], "reason": f"guard_query_failed:{type(error).__name__}"}
    invalid = []
    for index, (_logical_name, relation) in enumerate(relations):
        value = _row_value(row, relation, index) if row else False
        if bool(value):
            invalid.append(relation)
    return {
        "status": "ready" if not invalid else "blocked",
        "invalid_relations": invalid,
        "reason": None if not invalid else "complete_rows_violate_factor_contract",
    }


def _guard_index_status(
    connection: Any,
    relation_tablespaces: Mapping[str, str | None],
) -> dict[str, Any]:
    """Check the cheap invalid-row indexes before running the guard query."""
    relations = [
        relation
        for logical_name in ("canonical_bars_daily", "market_bars_daily", "daily_adjustment_factors")
        for relation in (logical_name, f"{logical_name}_cold")
        if relation in relation_tablespaces
    ]
    expected = [f"{relation}_semantic_guard_idx" for relation in relations]
    if not expected:
        return {"status": "error", "expected": [], "missing": [], "reason": "semantic_relations_missing"}
    try:
        rows = connection.execute(
            """SELECT indexname,indexdef FROM pg_indexes
                WHERE schemaname='quant' AND indexname=ANY(%s)""",
            (expected,),
        ).fetchall()
    except Exception as error:  # diagnostics must remain read-only and fail closed
        return {
            "status": "error", "expected": expected, "missing": expected,
            "reason": f"guard_index_query_failed:{type(error).__name__}",
        }
    definitions = {
        str(_row_value(row, "indexname", 0)): str(_row_value(row, "indexdef", 1) or "")
        for row in rows
    }
    present = set(definitions)
    missing = sorted(set(expected) - present)
    invalid = sorted(
        index_name for index_name, definition in definitions.items()
        if not _guard_index_definition_valid(index_name, definition)
    )
    return {
        "status": "ready" if not missing and not invalid else "blocked",
        "expected": expected,
        "present": sorted(present),
        "missing": missing,
        "invalid": invalid,
        "reason": (
            None if not missing and not invalid
            else "guard_indexes_missing" if missing
            else "guard_indexes_invalid"
        ),
    }


def _guard_index_definition_valid(index_name: str, definition: str) -> bool:
    """Ensure a same-named index really bounds the invalid-row probe.

    ``CREATE INDEX IF NOT EXISTS`` is intentionally idempotent, but PostgreSQL
    does not compare definitions when an index with that name already exists.
    Checking the predicate here prevents a malformed or stale replacement from
    making the health endpoint claim the cheap guard path is available while
    the query planner still has to scan the full history.
    """
    normalized = re.sub(r"\s+", " ", definition.lower()).strip()
    relation = index_name.removesuffix("_semantic_guard_idx")
    required = (
        f"on quant.{relation} ",
        "where ",
        "adjustment_state",
        "'complete'",
        "adj_factor",
    )
    if not all(token in normalized for token in required):
        return False
    is_factor_relation = relation == "daily_adjustment_factors" or (
        relation.startswith("daily_adjustment_factors_") and relation.endswith("_cold")
    )
    if is_factor_relation:
        provider_exclusion = (
            "not in" in normalized
            or "<> all" in normalized
            or "!= all" in normalized
            or "not (provider = any" in normalized
        )
        return (
            all(token in normalized for token in ("factor_semantics", "cumulative_tushare", "provider", "longhu_qfq_derived"))
            and provider_exclusion
        )
    return "is null" in normalized and "<=" in normalized


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    return row[index]


def _relation_tablespaces(connection: Any, names: Iterable[str]) -> dict[str, str | None]:
    rows = connection.execute(
        """SELECT tablename AS table_name,tablespace
             FROM pg_tables
            WHERE schemaname='quant' AND tablename=ANY(%s)""",
        (list(names),),
    ).fetchall()
    return {
        str(_row_value(row, "table_name")): (
            # Tuple rows are table_name,tablespace.  The old implicit index 0
            # made every non-null tablespace look like the table name and
            # permanently reported legacy_source_records:not_in_stock_cold.
            str(_row_value(row, "tablespace", 1)) if _row_value(row, "tablespace", 1) else None
        )
        for row in rows
    }


def _column_metadata(connection: Any, names: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    rows = connection.execute(
        """SELECT table_name,ordinal_position,column_name,udt_schema,udt_name,
                  character_maximum_length,numeric_precision,numeric_scale,
                  datetime_precision,is_nullable
             FROM information_schema.columns
            WHERE table_schema='quant' AND table_name=ANY(%s)
            ORDER BY table_name,ordinal_position""",
        (list(names),),
    ).fetchall()
    result: dict[str, list[dict[str, Any]]] = {}
    fields = (
        "ordinal_position", "column_name", "udt_schema", "udt_name",
        "character_maximum_length", "numeric_precision", "numeric_scale",
        "datetime_precision", "is_nullable",
    )
    for row in rows:
        table_name = str(_row_value(row, "table_name"))
        result.setdefault(table_name, []).append({
            field: _row_value(row, field, index + 1)
            for index, field in enumerate(fields)
        })
    return result


def _column_signature(rows: list[dict[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    """Return the ordered fields PostgreSQL needs for a safe SELECT * union."""
    return tuple(
        (
            row["ordinal_position"], row["column_name"], row["udt_schema"], row["udt_name"],
            row["character_maximum_length"], row["numeric_precision"], row["numeric_scale"],
            row["datetime_precision"],
        )
        for row in rows
    )


def classify_owner_layout(
    *,
    tablespace_present: bool,
    relation_tablespaces: Mapping[str, str | None],
    columns: Mapping[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Classify a catalog snapshot without mutating the owner database."""
    relation_details: list[dict[str, Any]] = []
    issues: list[str] = []
    present_cold: list[str] = []
    eligible_cold: list[str] = []

    if not tablespace_present:
        issues.append("stock_cold_tablespace_missing")

    for logical_name in TIERED_EVIDENCE_TABLES:
        cold_name = f"{logical_name}_cold"
        hot_present = logical_name in relation_tablespaces
        cold_present = cold_name in relation_tablespaces
        if cold_present:
            present_cold.append(cold_name)
        hot_signature = _column_signature(columns.get(logical_name, []))
        cold_signature = _column_signature(columns.get(cold_name, []))
        schema_compatible = bool(hot_signature) and hot_signature == cold_signature
        cold_tablespace = relation_tablespaces.get(cold_name)
        relation_issues: list[str] = []
        if not hot_present:
            relation_issues.append("hot_missing")
        if not cold_present:
            relation_issues.append("cold_missing")
        elif cold_tablespace != COLD_TABLESPACE:
            relation_issues.append("cold_not_in_stock_cold")
        if cold_present and not schema_compatible:
            relation_issues.append("schema_mismatch")
        eligible = (
            tablespace_present
            and hot_present
            and cold_present
            and cold_tablespace == COLD_TABLESPACE
            and schema_compatible
        )
        if eligible:
            eligible_cold.append(cold_name)
        issues.extend(f"{logical_name}:{issue}" for issue in relation_issues)
        relation_details.append({
            "logical_name": logical_name,
            "hot_present": hot_present,
            "cold_name": cold_name,
            "cold_present": cold_present,
            "cold_tablespace": cold_tablespace,
            "schema_compatible": schema_compatible,
            "read_eligible": eligible,
            "issues": relation_issues,
        })

    legacy_present = LEGACY_COLD_RELATION in relation_tablespaces
    legacy_tablespace = relation_tablespaces.get(LEGACY_COLD_RELATION)
    legacy_ready = legacy_present and legacy_tablespace == COLD_TABLESPACE
    if not legacy_present:
        issues.append("legacy_source_records:missing")
    elif not legacy_ready:
        issues.append("legacy_source_records:not_in_stock_cold")

    storage_ready = (
        tablespace_present
        and len(eligible_cold) == len(TIERED_EVIDENCE_TABLES)
        and legacy_ready
    )
    cutover_evidence = (
        tablespace_present
        or bool(present_cold)
        or legacy_tablespace == COLD_TABLESPACE
    )
    storage_state = (
        "layered" if storage_ready
        else "partial_cutover" if cutover_evidence
        else "legacy_hot_only"
    )

    column_lookup = {
        table_name: {str(row["column_name"]): row for row in table_columns}
        for table_name, table_columns in columns.items()
    }
    semantic_missing: dict[str, list[str]] = {}
    semantic_seen = 0
    for table_name, required in SEMANTIC_REQUIRED_COLUMNS.items():
        available = column_lookup.get(table_name, {})
        missing = [column_name for column_name in required if column_name not in available]
        semantic_seen += len(required) - len(missing)
        if missing:
            semantic_missing[table_name] = missing
    factor_columns = column_lookup.get("daily_adjustment_factors", {})
    factor_nullable = factor_columns.get("adj_factor", {}).get("is_nullable") == "YES"
    semantic_issues = [
        f"{table_name}:missing:{column_name}"
        for table_name, missing in semantic_missing.items()
        for column_name in missing
    ]
    if not factor_nullable:
        semantic_issues.append("daily_adjustment_factors:adj_factor_not_nullable")
    semantic_ready = not semantic_missing and factor_nullable
    semantic_guard = {
        "status": "not_evaluated",
        "invalid_relations": [],
        "reason": "catalog_only_projection",
    }
    semantic_state = (
        "ready" if semantic_ready
        else "legacy" if semantic_seen == 0
        else "partial"
    )
    issues.extend(f"adjustment_semantics:{issue}" for issue in semantic_issues)

    cutover_ready = storage_ready and semantic_ready
    overall_state = (
        "layered" if cutover_ready
        else "legacy_hot_only" if storage_state == "legacy_hot_only" and semantic_state == "legacy"
        else "partial_cutover"
    )
    hot_names = [*TIERED_EVIDENCE_TABLES, LEGACY_COLD_RELATION]
    return {
        "status": overall_state,
        "storage_state": storage_state,
        "owner_cutover_required": not cutover_ready,
        "cutover_ready": cutover_ready,
        "hot_schema": {
            "present_tables": sorted(name for name in hot_names if name in relation_tablespaces),
        },
        "cold_schema": {
            "tablespace": COLD_TABLESPACE if tablespace_present else None,
            "present_tables": sorted(present_cold),
            "eligible_tables": sorted(eligible_cold) if storage_ready else [],
            "expected_tables": [f"{name}_cold" for name in TIERED_EVIDENCE_TABLES],
            "relations": relation_details,
            "atomic_read_enabled": storage_ready,
        },
        "legacy_source_records": {
            "present": legacy_present,
            "tablespace": legacy_tablespace,
            "cold_policy": "whole_table_to_stock_cold",
            "ready": legacy_ready,
        },
        "adjustment_semantics": {
            "status": semantic_state,
            "ready": semantic_ready,
            "required_columns": SEMANTIC_REQUIRED_COLUMNS,
            "missing_columns": semantic_missing,
            "adj_factor_nullable": factor_nullable,
            "issues": semantic_issues,
            "guard_indexes": {
                "status": "not_evaluated",
                "expected": [],
                "missing": [],
                "reason": "catalog_only_projection",
            },
            "data_guard": semantic_guard,
        },
        "issues": sorted(set(issues)),
        "semantics": {
            "hot": "current_and_recent owner PostgreSQL rows",
            "cold": "rows older than 365 days in schema-compatible *_cold twins; read-only evidence",
            "peer_writes": "disabled; owner release pipeline owns storage DDL and tier jobs",
            "batch_tunnel": "5433 for COPY/backfill/backup; 5432 for intraday/session reads",
            "cold_read_activation": "atomic after all five twins, legacy placement and schema checks pass",
        },
    }


def _catalog_snapshot(connection: Any) -> dict[str, Any]:
    names = {
        *TIERED_EVIDENCE_TABLES,
        *(f"{name}_cold" for name in TIERED_EVIDENCE_TABLES),
        LEGACY_COLD_RELATION,
        "market_bars_daily",
    }
    tablespace_row = connection.execute(
        "SELECT EXISTS(SELECT 1 FROM pg_tablespace WHERE spcname=%s) AS present",
        (COLD_TABLESPACE,),
    ).fetchone()
    relation_tablespaces = _relation_tablespaces(connection, names)
    columns = _column_metadata(connection, names)
    status = classify_owner_layout(
        tablespace_present=bool(_row_value(tablespace_row, "present")) if tablespace_row else False,
        relation_tablespaces=relation_tablespaces,
        columns=columns,
    )
    semantic = status["adjustment_semantics"]
    guard_indexes = _guard_index_status(connection, relation_tablespaces) if semantic["ready"] is True else {
        "status": "not_evaluated", "expected": [], "missing": [], "reason": "semantic_columns_missing",
    }
    semantic["guard_indexes"] = guard_indexes
    guard = (
        _semantic_guard(connection, relation_tablespaces, True)
        if semantic["ready"] is True and guard_indexes["status"] == "ready"
        else {
            "status": "error" if semantic["ready"] is True else "not_evaluated",
            "invalid_relations": [],
            "reason": guard_indexes.get("reason") if semantic["ready"] is True else "semantic_columns_missing",
        }
    )
    semantic["data_guard"] = guard
    if semantic["ready"] is True and guard["status"] != "ready":
        semantic["ready"] = False
        semantic["status"] = "partial"
        semantic["issues"] = [*semantic.get("issues", []), "data_guard:nonzero_or_unavailable"]
        status["issues"] = sorted(set([
            *status.get("issues", []),
            "adjustment_semantics:data_guard:nonzero_or_unavailable",
        ]))
        status["cutover_ready"] = False
        status["owner_cutover_required"] = True
        status["status"] = "partial_cutover"
    return status


def _database_lineage(connection: Any) -> dict[str, Any]:
    """Return a non-secret identity shared by the API and batch lanes."""
    try:
        row = connection.execute(
            """SELECT current_database() AS database_name,
                       CASE WHEN to_regclass('quant.alembic_version') IS NULL THEN NULL
                            ELSE (SELECT version_num FROM quant.alembic_version
                                   ORDER BY version_num DESC LIMIT 1)
                       END AS alembic_version"""
        ).fetchone()
    except Exception as error:  # diagnostics must remain read-only and fail closed
        return {
            "database_name": None,
            "alembic_version": None,
            "status": "error",
            "reason": f"lineage_query_failed:{type(error).__name__}",
        }
    database_name = _row_value(row, "database_name") if row else None
    alembic_version = _row_value(row, "alembic_version", 1) if row else None
    return {
        "database_name": str(database_name) if database_name else None,
        "alembic_version": str(alembic_version) if alembic_version else None,
        "status": "ready" if database_name and alembic_version else "incomplete",
        "reason": None if database_name and alembic_version else "database_or_lineage_missing",
    }


def storage_tier_status(connection: Any) -> dict[str, Any]:
    """Return layout, semantics and settings without changing owner state."""
    status = _catalog_snapshot(connection)
    status["database_lineage"] = _database_lineage(connection)
    settings = connection.execute(
        """SELECT name,setting,unit FROM pg_settings
             WHERE name=ANY(%s)
             ORDER BY name""",
        ([
            "shared_buffers", "work_mem", "temp_file_limit", "random_page_cost",
            "track_io_timing", "log_lock_waits", "log_temp_files",
            "checkpoint_completion_target",
        ],),
    ).fetchall()
    status["settings"] = {
        str(_row_value(row, "name")): {
            "setting": _row_value(row, "setting", 1),
            "unit": _row_value(row, "unit", 2),
        }
        for row in settings
    }
    return status


def eligible_cold_tables(connection: Any) -> set[str]:
    """Return cold twins only after storage *and* semantic cutover is ready.

    Release 1 and Release 2 are intentionally separate owner operations.  A
    complete set of cold twins must not make a research query bypass the
    adjustment-state/data-guard gate while Release 2 is still being applied.
    """
    status = _catalog_snapshot(connection)
    if status["status"] != "layered":
        return set()
    return set(status["cold_schema"]["eligible_tables"])


def tiered_relation_sql(logical_name: str, cold_tables: set[str] | frozenset[str]) -> str:
    """Return a safe hot+cold relation only for a complete cold snapshot."""
    if logical_name not in TIERED_EVIDENCE_TABLES:
        raise ValueError(f"unsupported tiered evidence table: {logical_name}")
    expected = {f"{name}_cold" for name in TIERED_EVIDENCE_TABLES}
    hot = f"quant.{logical_name}"
    cold = f"quant.{logical_name}_cold"
    return (
        f"(SELECT * FROM {hot} UNION ALL SELECT * FROM {cold})"
        if expected.issubset(cold_tables)
        else hot
    )


def tiered_sql_builder(connection: Any, logical_names: Iterable[str] = TIERED_EVIDENCE_TABLES):
    """Bind one owner-layout snapshot to a group of research SQL statements.

    Catalog/semantic checks are intentionally performed once per transaction,
    not once per query. Callers use the returned pure transformer for every
    read in a study so a long operation cannot switch between hot-only and
    hot+cold SQL halfway through.
    """
    cold_tables = eligible_cold_tables(connection) if hasattr(connection, "cursor") else set()
    names = tuple(logical_names)

    def build(sql: str) -> str:
        transformed = sql
        for logical_name in names:
            transformed = transformed.replace(
                f"quant.{logical_name}", tiered_relation_sql(logical_name, cold_tables),
            )
        return transformed

    return build


__all__ = [
    "COLD_TABLESPACE", "LEGACY_COLD_RELATION", "SEMANTIC_REQUIRED_COLUMNS",
    "OWNER_RUNTIME_REQUIRED_COLUMNS", "TIERED_EVIDENCE_TABLES", "classify_owner_layout",
    "eligible_cold_tables", "owner_runtime_schema_status", "storage_tier_status",
    "tiered_relation_sql", "tiered_sql_builder",
]
