from pathlib import Path
import unittest


class RealtimeReadIndexesMigrationTests(unittest.TestCase):
    def test_migration_is_forward_from_owner_intraday_health_head_and_reversible(self):
        source = (Path(__file__).parents[1] / "migrations/versions/20261016_ow0123_realtime_read_indexes.py").read_text()
        self.assertIn('revision = "20261016_ow0123"', source)
        self.assertIn('down_revision = "20261015_ow0122"', source)
        for index in (
            "market_events_occurred_created_idx",
            "market_events_type_source_occurred_idx",
            "market_events_symbol_occurred_idx",
            "strategy_pattern_runs_latest_idx",
            "strategy_pattern_samples_run_rank_idx",
        ):
            self.assertIn(index, source)
            self.assertIn(f"DROP INDEX IF EXISTS quant.{index}", source)


if __name__ == "__main__":
    unittest.main()
