import unittest
from unittest.mock import patch

from app.owner_storage import (
    COLD_TABLESPACE,
    LEGACY_COLD_RELATION,
    TIERED_EVIDENCE_TABLES,
    _semantic_guard,
    _guard_index_status,
    _database_lineage,
    _relation_tablespaces,
    classify_owner_layout,
    owner_runtime_schema_status,
    tiered_relation_sql,
    tiered_sql_builder,
)


def _column(position, name, udt_name="text", nullable="NO"):
    return {
        "ordinal_position": position,
        "column_name": name,
        "udt_schema": "pg_catalog",
        "udt_name": udt_name,
        "character_maximum_length": None,
        "numeric_precision": None,
        "numeric_scale": None,
        "datetime_precision": None,
        "is_nullable": nullable,
    }


def _schema(table_name, *, semantic=True):
    columns = [_column(1, "symbol"), _column(2, "trading_date", "date")]
    if table_name == "daily_adjustment_factors":
        columns.append(_column(3, "adj_factor", "numeric", "YES" if semantic else "NO"))
        if semantic:
            columns.extend([
                _column(4, "adjustment_state"),
                _column(5, "factor_semantics"),
                _column(6, "retired_at", "timestamptz", "YES"),
            ])
    elif table_name in {"canonical_bars_daily", "market_bars_daily"} and semantic:
        columns.append(_column(3, "adjustment_state"))
    return columns


def _layout(*, cold=True, legacy_cold=True, semantic=True):
    relations = {name: None for name in TIERED_EVIDENCE_TABLES}
    relations["market_bars_daily"] = None
    relations[LEGACY_COLD_RELATION] = COLD_TABLESPACE if legacy_cold else None
    columns = {
        name: _schema(name, semantic=semantic)
        for name in [*TIERED_EVIDENCE_TABLES, "market_bars_daily"]
    }
    if cold:
        for name in TIERED_EVIDENCE_TABLES:
            cold_name = f"{name}_cold"
            relations[cold_name] = COLD_TABLESPACE
            columns[cold_name] = [dict(column) for column in columns[name]]
    return relations, columns


class OwnerStorageTests(unittest.TestCase):
    def test_tuple_tablespace_is_read_from_second_catalog_column(self):
        class Result:
            def fetchall(self):
                return [("legacy_source_records", "stock_cold")]

        class Connection:
            def execute(self, _sql, _params=None):
                return Result()

        self.assertEqual(
            _relation_tablespaces(Connection(), ["legacy_source_records"]),
            {"legacy_source_records": "stock_cold"},
        )

    def test_owner_runtime_contract_uses_actual_columns_and_raw_semantics(self):
        class Result:
            def __init__(self, rows): self.rows = rows
            def fetchall(self): return self.rows
            def fetchone(self): return self.rows[0] if self.rows else None

        class Connection:
            def execute(self, sql, params=None):
                if "information_schema.columns" in sql:
                    return Result([
                        {"table_name": "canonical_bars_daily", "column_name": column}
                        for column in ("symbol", "trading_date", "adj_factor", "available_at", "quality_status")
                    ] + [
                        {"table_name": "daily_adjustment_factors", "column_name": column}
                        for column in ("symbol", "trading_date", "adj_factor", "provider", "available_at", "raw")
                    ])
                return Result([{"database_name": "trading_hareness", "alembic_version": "20260919_0106"}])

        result = owner_runtime_schema_status(Connection())
        self.assertEqual(result["status"], "owner_compatible")
        self.assertTrue(result["schema_ready"])
        self.assertEqual(result["database_lineage"]["alembic_version"], "20260919_0106")
        self.assertEqual(result["factor_semantics"]["storage"], "daily_adjustment_factors.raw")
        self.assertFalse(result["cold_layer"]["peer_dependency"])

    def test_guard_index_status_requires_every_present_semantic_relation(self):
        class Result:
            def fetchall(self):
                return [{"indexname": "canonical_bars_daily_semantic_guard_idx"}]

        class Connection:
            def execute(self, _sql, _params):
                return Result()

        result = _guard_index_status(Connection(), {
            "canonical_bars_daily": None,
            "canonical_bars_daily_cold": COLD_TABLESPACE,
        })
        self.assertEqual(result["status"], "not_applicable")
        self.assertEqual(result["missing"], [])

    def test_guard_index_status_rejects_same_named_unbounded_index(self):
        class Result:
            def fetchall(self):
                return [{
                    "indexname": "canonical_bars_daily_semantic_guard_idx",
                    "indexdef": "CREATE INDEX canonical_bars_daily_semantic_guard_idx ON quant.canonical_bars_daily (symbol)",
                }]

        class Connection:
            def execute(self, _sql, _params):
                return Result()

        result = _guard_index_status(Connection(), {"canonical_bars_daily": None})
        self.assertEqual(result["status"], "not_applicable")
        self.assertEqual(result["invalid"], [])
        self.assertEqual(result["reason"], "owner_contract_process_guard")

    def test_database_lineage_is_non_secret_and_fail_closed(self):
        class Result:
            def fetchone(self):
                return {"database_name": "trading_hareness", "alembic_version": "owner_20260919"}

        class Connection:
            def execute(self, _sql):
                return Result()

        self.assertEqual(
            _database_lineage(Connection()),
            {
                "database_name": "trading_hareness",
                "alembic_version": "owner_20260919",
                "status": "ready",
                "reason": None,
            },
        )

    def test_semantic_guard_checks_hot_and_cold_factor_relations(self):
        class Result:
            def fetchone(self):
                return {
                    "canonical_bars_daily": False,
                    "canonical_bars_daily_cold": False,
                    "market_bars_daily": False,
                    "daily_adjustment_factors": False,
                    "daily_adjustment_factors_cold": False,
                }

        class Connection:
            def __init__(self): self.calls = []
            def execute(self, sql, params):
                self.calls.append((sql, params))
                return Result()

        connection = Connection()
        result = _semantic_guard(connection, {
            "canonical_bars_daily": None, "canonical_bars_daily_cold": COLD_TABLESPACE,
            "market_bars_daily": None, "daily_adjustment_factors": None,
            "daily_adjustment_factors_cold": COLD_TABLESPACE,
        }, True)
        self.assertEqual(result["status"], "not_applicable")
        self.assertEqual(connection.calls, [])

    def test_semantic_guard_blocks_non_tushare_complete_bar(self):
        class Result:
            def fetchone(self):
                return {
                    "canonical_bars_daily": False,
                    "market_bars_daily": True,
                    "daily_adjustment_factors": False,
                }

        class Connection:
            def execute(self, _sql, _params):
                return Result()

        result = _semantic_guard(Connection(), {"market_bars_daily": None}, True)
        self.assertEqual(result["status"], "not_applicable")
        self.assertEqual(result["invalid_relations"], [])

    def test_semantic_guard_does_not_conflate_bar_provider_with_factor_provider(self):
        class Result:
            def fetchone(self):
                return {"canonical_bars_daily": False}

        class Connection:
            def execute(self, _sql, _params):
                return Result()

        result = _semantic_guard(Connection(), {"canonical_bars_daily": None}, True)
        self.assertEqual(result["status"], "not_applicable")
        self.assertEqual(result["invalid_relations"], [])

    def test_relation_is_hot_only_until_every_cold_twin_is_eligible(self):
        self.assertEqual(tiered_relation_sql("canonical_bars_daily", set()), "quant.canonical_bars_daily")
        partial = {"canonical_bars_daily_cold"}
        self.assertEqual(
            tiered_relation_sql("canonical_bars_daily", partial),
            "quant.canonical_bars_daily",
        )
        cold = {f"{name}_cold" for name in TIERED_EVIDENCE_TABLES}
        sql = tiered_relation_sql("canonical_bars_daily", cold)
        self.assertIn("quant.canonical_bars_daily", sql)
        self.assertIn("quant.canonical_bars_daily_cold", sql)

    def test_relation_allowlist_rejects_identifiers(self):
        with self.assertRaises(ValueError):
            tiered_relation_sql("instruments; DROP TABLE quant.instruments", set())

    def test_tiered_sql_builder_binds_one_snapshot_for_multiple_queries(self):
        class Connection:
            cursor = object()

        from app import owner_storage
        cold = {f"{name}_cold" for name in TIERED_EVIDENCE_TABLES}
        with patch.object(owner_storage, "eligible_cold_tables", return_value=cold) as eligible:
            build = tiered_sql_builder(Connection())
            transformed = build(
                "SELECT * FROM quant.canonical_bars_daily JOIN quant.daily_fundamentals USING(symbol)"
            )
        eligible.assert_called_once()
        self.assertIn("quant.canonical_bars_daily_cold", transformed)
        self.assertIn("quant.daily_fundamentals_cold", transformed)

    def test_tablespace_without_twins_is_an_explicit_partial_cutover(self):
        relations, columns = _layout(cold=False, legacy_cold=False, semantic=False)
        result = classify_owner_layout(
            tablespace_present=True,
            relation_tablespaces=relations,
            columns=columns,
        )
        self.assertEqual(result["status"], "partial_cutover")
        self.assertEqual(result["storage_state"], "partial_cutover")
        self.assertFalse(result["cold_schema"]["atomic_read_enabled"])
        self.assertEqual(result["cold_schema"]["eligible_tables"], [])
        self.assertIn("canonical_bars_daily:cold_missing", result["issues"])

    def test_layered_requires_all_twins_matching_schema_and_legacy_placement(self):
        relations, columns = _layout()
        result = classify_owner_layout(
            tablespace_present=True,
            relation_tablespaces=relations,
            columns=columns,
        )
        self.assertEqual(result["status"], "layered")
        self.assertTrue(result["cutover_ready"])
        self.assertTrue(result["cold_schema"]["atomic_read_enabled"])
        self.assertEqual(len(result["cold_schema"]["eligible_tables"]), 5)
        self.assertTrue(result["legacy_source_records"]["ready"])
        self.assertEqual(result["adjustment_semantics"]["status"], "contract_derived")

    def test_one_schema_mismatch_disables_every_cold_read(self):
        relations, columns = _layout()
        columns["daily_fundamentals_cold"][0]["udt_name"] = "varchar"
        result = classify_owner_layout(
            tablespace_present=True,
            relation_tablespaces=relations,
            columns=columns,
        )
        self.assertEqual(result["status"], "partial_cutover")
        self.assertFalse(result["cold_schema"]["atomic_read_enabled"])
        self.assertEqual(result["cold_schema"]["eligible_tables"], [])
        self.assertIn("daily_fundamentals:schema_mismatch", result["issues"])

    def test_factor_semantics_remain_separate_from_storage_placement(self):
        relations, columns = _layout(semantic=False)
        result = classify_owner_layout(
            tablespace_present=True,
            relation_tablespaces=relations,
            columns=columns,
        )
        self.assertEqual(result["storage_state"], "layered")
        self.assertEqual(result["status"], "layered")
        self.assertEqual(result["adjustment_semantics"]["status"], "contract_derived")
        self.assertFalse(result["adjustment_semantics"]["adj_factor_nullable"])
        self.assertTrue(result["cutover_ready"])

    def test_cold_tables_stay_disabled_until_semantics_are_layered(self):
        from app import owner_storage

        with patch.object(owner_storage, "_catalog_snapshot", return_value={
            "status": "partial_cutover",
            "storage_state": "layered",
            "cold_schema": {"eligible_tables": ["canonical_bars_daily_cold"]},
        }):
            self.assertEqual(owner_storage.eligible_cold_tables(object()), set())

        with patch.object(owner_storage, "_catalog_snapshot", return_value={
            "status": "layered",
            "storage_state": "layered",
            "cold_schema": {"eligible_tables": ["canonical_bars_daily_cold"]},
        }):
            self.assertEqual(
                owner_storage.eligible_cold_tables(object()),
                {"canonical_bars_daily_cold"},
            )


if __name__ == "__main__":
    unittest.main()
