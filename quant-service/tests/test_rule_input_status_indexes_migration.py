from pathlib import Path
import unittest


class RuleInputStatusIndexesMigrationTests(unittest.TestCase):
    def test_migration_is_forward_from_realtime_read_head_and_has_reversible_partial_indexes(self):
        source = (Path(__file__).parents[1] / "migrations/versions/20261016_ow0124_rule_input_status_indexes.py").read_text()
        self.assertIn('revision = "20261016_ow0124"', source)
        self.assertIn('down_revision = "20261016_ow0123"', source)
        for version in ("v1", "v2"):
            index = f"intraday_rule_input_snapshot_{version}_idx"
            self.assertIn(index, source)
            self.assertIn(f"DROP INDEX IF EXISTS quant.{index}", source)
            self.assertIn(f"intraday-rule-input-{version}", source)


if __name__ == "__main__":
    unittest.main()
