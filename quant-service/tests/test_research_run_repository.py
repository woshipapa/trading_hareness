"""Pure and SQL-boundary tests for the append-only research run ledger."""

from datetime import date, datetime, timezone
from unittest.mock import MagicMock
import unittest

from app.research_run_repository import (
    finish_research_run,
    research_output_digest,
    start_research_run,
)


class ResearchRunRepositoryTests(unittest.TestCase):
    def test_output_digest_is_stable_for_mapping_order(self):
        self.assertEqual(
            research_output_digest({"metrics": {"ic": 0.1, "days": 20}}),
            research_output_digest({"metrics": {"days": 20, "ic": 0.1}}),
        )

    def test_start_run_records_the_availability_cutoff_and_inputs(self):
        connection = MagicMock()
        cutoff = datetime(2026, 8, 27, 8, tzinfo=timezone.utc)

        run_id = start_research_run(
            connection,
            experiment_type="factor_evaluation",
            universe_key="core",
            start_date=date(2025, 1, 2),
            end_date=date(2026, 8, 27),
            knowledge_cutoff=cutoff,
            parameters={"factor_keys": ["momentum_20d"]},
            input_datasets=("canonical_bars_daily", "universe_membership_history"),
        )

        self.assertRegex(str(run_id), r"^[0-9a-f-]{36}$")
        sql, params = connection.execute.call_args_list[0].args
        self.assertIn("INSERT INTO quant.research_experiment_runs", sql)
        self.assertEqual(params[7], cutoff)
        edge_sql, edge_params = connection.execute.call_args_list[-1].args
        self.assertIn("INSERT INTO quant.research_lineage_edges", edge_sql)
        self.assertEqual(edge_params[1], "universe_membership_history")
        self.assertEqual(connection.execute.call_count, 3)

    def test_finish_run_writes_output_digest_and_status(self):
        connection = MagicMock()
        run_id = start_research_run(
            connection,
            experiment_type="strategy_backtest",
            universe_key="core",
            start_date=date(2025, 1, 2),
            end_date=date(2026, 8, 27),
            knowledge_cutoff=datetime(2026, 8, 27, 8, tzinfo=timezone.utc),
            parameters={},
            input_datasets=(),
        )
        connection.reset_mock()

        digest = finish_research_run(connection, run_id, status="completed", output={"return": 0.12})

        self.assertEqual(len(digest), 64)
        sql, params = connection.execute.call_args_list[0].args
        self.assertIn("UPDATE quant.research_experiment_runs", sql)
        self.assertEqual(params[0], "completed")
        self.assertEqual(params[1], digest)
        self.assertEqual(params[-1], run_id)

    def test_manifest_backed_run_uses_real_snapshot_content_digest(self):
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = {
            "snapshot_key": "snapshot-1",
            "content_sha256": "a" * 64,
            "manifest": {"as_of_date": "2026-08-27", "equity_symbols": 5500},
            "manifest_version": "research-manifest-v2",
            "code_sha": "code-sha",
            "data_schema_version": "feature-availability-cutoff-v2",
        }
        start_research_run(
            connection,
            experiment_type="factor_evaluation",
            universe_key="core",
            start_date=date(2025, 1, 2), end_date=date(2026, 8, 27),
            knowledge_cutoff=datetime(2026, 8, 27, 8, tzinfo=timezone.utc),
            parameters={}, input_datasets=("canonical_bars_daily",), data_manifest_id="snapshot-1",
        )
        edge_sql, edge_params = connection.execute.call_args_list[-1].args
        self.assertIn("research_lineage_edges", edge_sql)
        self.assertEqual(edge_params[3], "a" * 64)
        self.assertEqual(edge_params[4]["manifest_content_sha256"], "a" * 64)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
