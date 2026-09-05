from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from app.async_research_catalog_read_repository import research_run as async_research_run
from app.async_research_catalog_read_repository import research_runs as async_research_runs
from app.research_catalog_read_model import research_run, research_runs
from app.research_catalog_read_model import latest_strategy_day_summary
from app.routers.research_catalog_reads import build_research_catalog_reads_router


class _AsyncTransaction:
    def __init__(self, connection: object):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return False


class ResearchRunReadTests(unittest.TestCase):
    def test_history_filters_and_bounds_page_size(self):
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = [{"research_run_id": UUID(int=1)}]
        database = MagicMock()
        database.transaction.return_value.__enter__.return_value = connection

        payload = research_runs(database, "factor_evaluation", "completed", 10_000)

        self.assertEqual(payload["items"], [{"research_run_id": UUID(int=1)}])
        self.assertTrue(payload["research_only"])
        sql, params = connection.execute.call_args.args
        self.assertIn("FROM quant.research_experiment_runs", sql)
        self.assertIn("experiment_type=%s AND status=%s", sql)
        self.assertEqual(params, ("factor_evaluation", "completed", 200))

    def test_result_projections_select_the_research_run_link(self):
        from app.research_catalog_read_model import factor_evaluations, strategy_experiments

        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = []
        database = MagicMock()
        database.transaction.return_value.__enter__.return_value = connection

        factor_evaluations(database, "core", 5)
        strategy_experiments(database, "core", 5)

        sqls = [call.args[0] for call in connection.execute.call_args_list]
        self.assertIn("e.research_run_id", sqls[0])
        self.assertIn("e.research_run_id", sqls[1])

    def test_latest_daily_summary_is_a_read_only_learning_receipt(self):
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = {
            "exchange_date": date(2026, 8, 21), "payload": {"readiness": {"decision_ready": False}},
        }
        database = MagicMock()
        database.transaction.return_value.__enter__.return_value = connection

        payload = latest_strategy_day_summary(database, date(2026, 8, 21))

        self.assertEqual(payload["summary"]["exchange_date"], date(2026, 8, 21))
        self.assertTrue(payload["research_only"])
        self.assertEqual(connection.execute.call_args.args[1], (date(2026, 8, 21),))

    def test_detail_returns_lineage_and_missing_run_is_explicit(self):
        connection = MagicMock()
        connection.execute.side_effect = [
            MagicMock(fetchone=MagicMock(return_value={"research_run_id": UUID(int=1), "status": "completed"})),
            MagicMock(fetchall=MagicMock(return_value=[{"direction": "input", "dataset_key": "canonical_bars_daily"}])),
        ]
        database = MagicMock()
        database.transaction.return_value.__enter__.return_value = connection

        payload = research_run(database, UUID(int=1))

        self.assertEqual(payload["run"]["status"], "completed")
        self.assertEqual(payload["lineage"][0]["direction"], "input")
        self.assertEqual(connection.execute.call_count, 2)

        connection.execute.side_effect = [MagicMock(fetchone=MagicMock(return_value=None))]
        missing = research_run(database, UUID(int=2))
        self.assertIsNone(missing["run"])
        self.assertEqual(missing["lineage"], [])

    def test_router_exposes_history_and_detail_as_get_only(self):
        router = build_research_catalog_reads_router(MagicMock())
        methods_by_path = {route.path: route.methods for route in router.routes}
        self.assertEqual(methods_by_path["/api/v1/research/runs"], {"GET"})
        self.assertEqual(methods_by_path["/api/v1/research/runs/{research_run_id}"], {"GET"})
        self.assertEqual(methods_by_path["/api/v1/strategy/daily-summary/latest"], {"GET"})


class AsyncResearchRunReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_history_and_detail_use_read_pool(self):
        connection = MagicMock()
        history_result = MagicMock()
        history_result.fetchall = AsyncMock(return_value=[{"research_run_id": UUID(int=3)}])
        detail_result = MagicMock()
        detail_result.fetchone = AsyncMock(return_value={"research_run_id": UUID(int=3), "status": "completed"})
        lineage_result = MagicMock()
        lineage_result.fetchall = AsyncMock(return_value=[])
        connection.execute = AsyncMock(side_effect=[history_result, detail_result, lineage_result])
        database = MagicMock()
        database.transaction.return_value = _AsyncTransaction(connection)

        history = await async_research_runs(database, status="completed", limit=1)
        detail = await async_research_run(database, UUID(int=3))

        self.assertEqual(history["items"][0]["research_run_id"], UUID(int=3))
        self.assertEqual(detail["run"]["status"], "completed")
        self.assertEqual(connection.execute.await_count, 3)
        self.assertEqual(connection.execute.await_args_list[0].args[1], ("completed", 1))

    async def test_async_latest_daily_summary_uses_read_pool(self):
        from app.async_research_catalog_read_repository import latest_strategy_day_summary as async_latest_summary

        connection = MagicMock()
        result = MagicMock()
        result.fetchone = AsyncMock(return_value={"exchange_date": date(2026, 8, 21)})
        connection.execute = AsyncMock(return_value=result)
        database = MagicMock()
        database.transaction.return_value = _AsyncTransaction(connection)

        payload = await async_latest_summary(database)

        self.assertEqual(payload["summary"]["exchange_date"], date(2026, 8, 21))
        self.assertEqual(connection.execute.await_args.args[1], ())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
