from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock
from uuid import UUID

from fastapi import HTTPException

from app.research_experiment_service import (
    ResearchExperimentDependencies,
    build_snapshot,
    evaluate_factors,
    latest_data_manifest_id,
    research_window,
)
from app.point_in_time import exchange_day_end


class _Transaction:
    def __init__(self, execute):
        self.execute = execute

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _deps(database: MagicMock, **overrides):
    values = {
        "database": database,
        "china_today": lambda: date(2026, 8, 21),
        "as_utc": lambda value: value,
        "http_exception": HTTPException,
        "evaluate_factor_set": MagicMock(),
        "run_multi_factor_strategy": MagicMock(),
        "json_value": lambda value: value,
    }
    values.update(overrides)
    return ResearchExperimentDependencies(**values)


class ResearchExperimentServiceTests(unittest.TestCase):
    def test_latest_manifest_prefers_ready_snapshot_at_the_cutoff(self):
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = {"snapshot_key": "snapshot-2026-08-21"}

        manifest_id = latest_data_manifest_id(
            connection, date(2026, 8, 21), datetime(2026, 8, 21, tzinfo=timezone.utc),
        )

        self.assertEqual(manifest_id, "snapshot-2026-08-21")
        sql, params = connection.execute.call_args.args
        self.assertIn("status IN ('ready','blocked')", sql)
        self.assertEqual(params[0], date(2026, 8, 21))

    def test_research_window_keeps_persisted_point_in_time_bounds(self) -> None:
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = {
            "earliest": date(2025, 1, 2), "latest": date(2026, 8, 21),
        }

        start, end = research_window(connection, "core", None, None, http_exception=HTTPException)

        self.assertEqual(start, date(2025, 1, 2))
        self.assertEqual(end, date(2026, 8, 21))
        self.assertIn("universe_membership_history", str(connection.execute.call_args.args[0]))
        self.assertIn("quality_status='fresh'", str(connection.execute.call_args.args[0]))

    def test_factor_evaluation_rejects_unknown_factor_before_running_sql_engine(self) -> None:
        connection = MagicMock()
        connection.execute.side_effect = [
            MagicMock(fetchone=MagicMock(return_value={"earliest": date(2026, 1, 2), "latest": date(2026, 8, 21)})),
            MagicMock(fetchall=MagicMock(return_value=[{"factor_key": "momentum_20d"}])),
        ]
        database = MagicMock()
        database.transaction.return_value = _Transaction(connection.execute)
        evaluator = MagicMock()
        payload = SimpleNamespace(
            universe_key="core", start_date=None, end_date=None, factor_keys=["not_registered"], horizon_days=5,
        )

        with self.assertRaises(HTTPException) as raised:
            evaluate_factors(payload, _deps(database, evaluate_factor_set=evaluator))

        self.assertEqual(raised.exception.status_code, 422)
        evaluator.assert_not_called()

    def test_factor_evaluation_returns_the_research_run_identity(self) -> None:
        connection = MagicMock()
        evaluation_row = MagicMock(fetchone=MagicMock(return_value={"evaluation_id": UUID(int=1)}))
        connection.execute.side_effect = [
            MagicMock(fetchone=MagicMock(return_value={
                "earliest": date(2025, 1, 2), "latest": date(2026, 8, 21),
            })),
            MagicMock(fetchall=MagicMock(return_value=[{"factor_key": "momentum_20d"}])),
            MagicMock(fetchone=MagicMock(return_value=None)), MagicMock(), MagicMock(), MagicMock(), MagicMock(),
            MagicMock(), MagicMock(), MagicMock(), evaluation_row,
            MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock(),
        ]
        database = MagicMock()
        database.transaction.return_value = _Transaction(connection.execute)
        evaluator = MagicMock(return_value=[{
            "factor_key": "momentum_20d", "status": "completed", "observations": 50,
            "cross_section_days": 20, "metrics": {}, "artifact": {},
        }])
        payload = SimpleNamespace(
            universe_key="core", start_date=None, end_date=None, factor_keys=["momentum_20d"], horizon_days=5,
        )

        result = evaluate_factors(payload, _deps(database, evaluate_factor_set=evaluator))

        self.assertRegex(result["research_run_id"], r"^[0-9a-f-]{36}$")
        self.assertEqual(len(result["output_digest"]), 64)
        self.assertIsNone(result["data_manifest_id"])
        self.assertTrue(any(
            len(call.args) > 1 and "sector_membership_history" in str(call.args[1])
            for call in connection.execute.call_args_list
        ))
        factor_insert = next(
            call for call in connection.execute.call_args_list
            if "INSERT INTO quant.factor_evaluations" in str(call.args[0])
        )
        self.assertEqual(factor_insert.args[1][-1], UUID(result["research_run_id"]))
        evaluator.assert_called_once()

    def test_snapshot_keeps_missing_daily_controls_blocked(self) -> None:
        manifest = {
            "bars": 10, "remote_reports": 2, "benchmark_bars": 1, "equity_symbols": 2,
            "fundamental_symbols": 1, "limit_symbols": 2, "exchange_open": True, "blocking_issues": 0,
        }
        statements = []

        def execute(sql, params=()):
            statements.append((str(sql), params))
            if "SELECT (SELECT count(*)::int FROM quant.canonical_bars_daily" in str(sql):
                return MagicMock(fetchone=MagicMock(return_value=manifest))
            return MagicMock()

        database = MagicMock()
        database.transaction.return_value = _Transaction(execute)
        payload = SimpleNamespace(
            as_of_date=date(2026, 8, 21), knowledge_cutoff=datetime(2026, 8, 21, tzinfo=timezone.utc),
        )

        result = build_snapshot(payload, _deps(database))

        self.assertEqual(result["status"], "blocked")
        inserts = [params for sql, params in statements if "INSERT INTO quant.data_snapshots" in sql]
        self.assertEqual(len(inserts), 1)
        self.assertEqual(inserts[0][3], "blocked")

    def test_snapshot_without_cutoff_uses_the_exchange_day_end(self) -> None:
        manifest = {
            "bars": 10, "remote_reports": 2, "benchmark_bars": 1, "equity_symbols": 2,
            "fundamental_symbols": 2, "limit_symbols": 2, "exchange_open": True, "blocking_issues": 0,
        }

        statements = []

        def execute(sql, params=()):
            statements.append((str(sql), params))
            if "SELECT (SELECT count(*)::int FROM quant.canonical_bars_daily" in str(sql):
                return MagicMock(fetchone=MagicMock(return_value=manifest))
            return MagicMock()

        database = MagicMock()
        database.transaction.return_value = _Transaction(execute)
        payload = SimpleNamespace(as_of_date=date(2026, 8, 21), knowledge_cutoff=None)

        build_snapshot(payload, _deps(database))

        manifest_query, manifest_params = next(
            (sql, params) for sql, params in statements
            if "SELECT (SELECT count(*)::int FROM quant.canonical_bars_daily" in sql
        )
        self.assertIn("trading_date<=%s AND available_at<=%s", manifest_query)
        self.assertIn("quality_status='fresh'", manifest_query)
        self.assertIn("basic.available_at<=%s", manifest_query)
        self.assertIn("limits.available_at<=%s", manifest_query)
        self.assertIn("calendar_date=%s AND available_at<=%s", manifest_query)
        self.assertEqual(len(manifest_params), 15)
        self.assertEqual(manifest_params[1], exchange_day_end(date(2026, 8, 21)))
        snapshot_insert = next(params for sql, params in statements if "INSERT INTO quant.data_snapshots" in sql)
        self.assertEqual(snapshot_insert[2], exchange_day_end(date(2026, 8, 21)))
