"""Startup control-plane runtime coverage without provider transport."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from types import SimpleNamespace
import unittest

from app.provider_control_plane_runtime import (
    CATALOG_PROJECTION_LOCK,
    ProviderControlPlaneRuntime,
    ProviderControlPlaneRuntimeDependencies,
)


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, *, lock_acquired: bool = True):
        self.calls = []
        self._lock_acquired = lock_acquired

    def execute(self, statement, params):
        self.calls.append((statement, params))
        if "pg_try_advisory_xact_lock" in statement:
            return _Cursor({"acquired": self._lock_acquired})
        return _Cursor(None)


def _item(api_name: str) -> dict:
    return {"api_name": api_name, "catalog_origin": "official", "permission_model": "points", "min_points": 1,
            "request_policy": "bounded", "model_role": "research", "priority": "high"}


def _runtime(connection, items=None) -> ProviderControlPlaneRuntime:
    @contextmanager
    def transaction():
        yield connection

    primary = SimpleNamespace(key="tushare_primary", rate_limit_per_minute=60)
    super_sdk = SimpleNamespace(key="tushare_super_sdk", rate_limit_per_minute=30)
    return ProviderControlPlaneRuntime(ProviderControlPlaneRuntimeDependencies(
        database=SimpleNamespace(transaction=transaction),
        provider_configs=lambda: {"primary": primary, "super_sdk": super_sdk},
        catalog_items=lambda: items if items is not None else [_item("daily"), _item("stock_basic")],
        capability_contract=lambda _name: SimpleNamespace(frequency="60/min", decision_eligible=False, note="declared"),
        super_get_verified_apis=frozenset({"daily"}),
    ))


class ProviderControlPlaneRuntimeTests(unittest.TestCase):
    def test_declares_the_whole_matrix_in_one_sorted_statement(self):
        connection = _Connection()
        self.assertTrue(_runtime(connection).initialize())
        statements = [statement for statement, _ in connection.calls]
        self.assertIn("pg_try_advisory_xact_lock", statements[0])
        self.assertEqual(connection.calls[0][1], (CATALOG_PROJECTION_LOCK,))
        # The retired primary route is not mirrored; the SDK limit is.
        self.assertEqual(connection.calls[1][1], (30, "tushare_super_sdk"))
        self.assertEqual(connection.calls[2][1], (30, "tushare_super_sdk"))
        bulk = [params for statement, params in connection.calls if "unnest" in statement]
        self.assertEqual(len(bulk), 1, "every declaration must travel in one statement")
        provider_keys, api_names = bulk[0][0], bulk[0][1]
        self.assertEqual(list(zip(provider_keys, api_names)), [
            ("tushare_backup", "stock_basic"), ("tushare_super_get", "daily"),
            ("tushare_super_sdk", "daily"), ("tushare_super_sdk", "stock_basic"),
        ])
        self.assertEqual(len(connection.calls), 4)

    def test_another_process_holding_the_lock_means_this_one_writes_nothing(self):
        connection = _Connection(lock_acquired=False)
        self.assertFalse(_runtime(connection).initialize())
        self.assertEqual(len(connection.calls), 1)

    def test_a_repeated_catalog_item_is_declared_once(self):
        connection = _Connection()
        _runtime(connection, [_item("daily"), _item("daily")]).initialize()
        bulk = next(params for statement, params in connection.calls if "unnest" in statement)
        self.assertEqual(list(zip(bulk[0], bulk[1])), [("tushare_super_get", "daily"), ("tushare_super_sdk", "daily")])

    def test_metadata_is_valid_json(self):
        declarations = _runtime(_Connection()).declarations()
        self.assertEqual(json.loads(declarations[0][5])["catalog_origin"], "official")


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class ProviderControlPlaneSqlTests(unittest.TestCase):
    """The bulk statement and the projection lock against a real schema; everything is rolled back."""

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(
            host=os.getenv("PGHOST"), port=os.getenv("PGPORT", "5432"), dbname=os.getenv("PGDATABASE", "n8n"),
            user=os.getenv("PGUSER", "n8n"), password=os.getenv("PGPASSWORD", ""), row_factory=dict_row,
        )

    def setUp(self) -> None:
        self.connection = self._connect()

    def tearDown(self) -> None:
        self.connection.rollback()
        self.connection.close()

    def test_the_bulk_projection_lands_and_merges_metadata(self):
        self.connection.execute(
            """INSERT INTO quant.provider_api_capabilities(provider_key,api_name,availability,frequency,decision_eligible,note,metadata)
               VALUES('tushare_super_sdk','zz_test_api','declared','old',true,'n','{"kept":1}'::jsonb)
               ON CONFLICT(provider_key,api_name) DO UPDATE SET metadata='{"kept":1}'::jsonb""")
        self.assertTrue(_runtime(self.connection, [_item("zz_test_api")]).initialize())
        row = self.connection.execute(
            "SELECT frequency,decision_eligible,metadata FROM quant.provider_api_capabilities "
            "WHERE provider_key='tushare_super_sdk' AND api_name='zz_test_api'").fetchone()
        self.assertEqual(row["frequency"], "60/min")
        self.assertFalse(row["decision_eligible"])
        self.assertEqual(row["metadata"]["kept"], 1)
        self.assertEqual(row["metadata"]["priority"], "high")

    def test_a_second_process_skips_while_the_first_projects(self):
        holder = self._connect()
        try:
            holder.execute("SELECT pg_advisory_xact_lock(%s)", (CATALOG_PROJECTION_LOCK,))
            self.assertFalse(_runtime(self.connection, [_item("zz_test_api_2")]).initialize())
            written = self.connection.execute(
                "SELECT count(*) AS n FROM quant.provider_api_capabilities WHERE api_name='zz_test_api_2'").fetchone()
            self.assertEqual(written["n"], 0)
        finally:
            holder.rollback()
            holder.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
