import unittest
from pathlib import Path


class OwnerGuardIndexesMigrationTests(unittest.TestCase):
    def test_guard_index_migration_covers_hot_and_existing_cold_twins(self):
        path = Path(__file__).parents[1] / "migrations/versions/20260919_ds0005_owner_guard_indexes.py"
        source = path.read_text(encoding="utf-8")
        for relation in (
            "canonical_bars_daily", "canonical_bars_daily_cold",
            "market_bars_daily", "market_bars_daily_cold",
            "daily_adjustment_factors", "daily_adjustment_factors_cold",
        ):
            self.assertIn(relation, source)
        self.assertIn("to_regclass('quant.{relation}')", source)
        self.assertIn("semantic_guard_idx", source)
        self.assertIn("factor_semantics IS DISTINCT FROM 'cumulative_tushare'", source)
        self.assertIn("provider NOT IN", source)

    def test_guard_index_migration_is_idempotent_and_reversible(self):
        path = Path(__file__).parents[1] / "migrations/versions/20260919_ds0005_owner_guard_indexes.py"
        source = path.read_text(encoding="utf-8")
        self.assertIn("CREATE INDEX IF NOT EXISTS", source)
        self.assertIn("DROP INDEX IF EXISTS", source)
        self.assertIn('down_revision = "20260919_ds0004"', source)


if __name__ == "__main__":
    unittest.main()
