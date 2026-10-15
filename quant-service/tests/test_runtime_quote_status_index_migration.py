from pathlib import Path
import unittest


class RuntimeQuoteStatusIndexMigrationTests(unittest.TestCase):
    def test_migration_is_forward_from_rule_input_status_head_and_reversible(self):
        source = (Path(__file__).parents[1] / "migrations/versions/20261016_ow0125_runtime_quote_status_index.py").read_text()
        self.assertIn('revision = "20261016_ow0125"', source)
        self.assertIn('down_revision = "20261016_ow0124"', source)
        self.assertIn("intraday_quote_status_source_observed_idx", source)
        self.assertIn("DROP INDEX IF EXISTS quant.intraday_quote_status_source_observed_idx", source)


if __name__ == "__main__":
    unittest.main()
