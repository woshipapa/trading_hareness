from pathlib import Path
import unittest


class QuoteFreshnessIndexMigrationTests(unittest.TestCase):
    def test_migration_is_forward_from_runtime_quote_status_head_and_reversible(self):
        source = (Path(__file__).parents[1] / "migrations/versions/20261016_ow0126_quote_freshness_index.py").read_text()
        self.assertIn('revision = "20261016_ow0126"', source)
        self.assertIn('down_revision = "20261016_ow0125"', source)
        self.assertIn("intraday_quote_observed_at_idx", source)
        self.assertIn("DROP INDEX IF EXISTS quant.intraday_quote_observed_at_idx", source)


if __name__ == "__main__":
    unittest.main()
