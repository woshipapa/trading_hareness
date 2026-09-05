from pathlib import Path
import unittest


class ReplayReadinessMigrationTest(unittest.TestCase):
    def test_adds_fresh_daily_and_limit_pit_indexes(self):
        path = Path(__file__).parents[1] / "migrations" / "versions" / "20260905_0092_replay_readiness_indexes.py"
        source = path.read_text()
        self.assertIn('revision = "20260905_0092"', source)
        self.assertIn('down_revision = "20260905_0091"', source)
        self.assertIn("canonical_bars_replay_fresh_date_idx", source)
        self.assertIn("WHERE quality_status='fresh'", source)
        self.assertIn("daily_trade_limits_pit_lookup_idx", source)
        self.assertIn("available_at DESC", source)

    def test_readiness_query_has_a_bounded_statement_timeout(self):
        source = (Path(__file__).parents[1] / "app" / "replay_readiness.py").read_text()
        self.assertIn("READINESS_STATEMENT_TIMEOUT_MS = 8000", source)
        self.assertIn("SET LOCAL statement_timeout", source)


if __name__ == "__main__":
    unittest.main()
