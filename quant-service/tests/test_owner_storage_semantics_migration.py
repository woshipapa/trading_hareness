import unittest
from pathlib import Path


class OwnerStorageSemanticsMigrationTests(unittest.TestCase):
    def test_migration_declares_four_states_and_identity_retirement(self):
        path = Path(__file__).parents[1] / "migrations/versions/20260919_ds0004_owner_storage_semantics.py"
        source = path.read_text(encoding="utf-8")
        for state in ("complete", "pending", "retired", "absent"):
            self.assertIn(state, source)
        self.assertIn("same_day_identity_only", source)
        self.assertIn("DROP NOT NULL", source)

    def test_migration_updates_cold_twins_created_by_the_prior_release(self):
        path = Path(__file__).parents[1] / "migrations/versions/20260919_ds0004_owner_storage_semantics.py"
        source = path.read_text(encoding="utf-8")
        self.assertIn('f"{table}_cold"', source)
        self.assertIn('"daily_adjustment_factors_cold"', source)
        self.assertIn("to_regclass('quant.{relation}')", source)
        self.assertIn("{relation}_research_state_idx", source)
        self.assertGreaterEqual(source.count('f"{table}_cold"'), 2)

    def test_migration_does_not_promote_untrusted_legacy_bar_factors(self):
        path = Path(__file__).parents[1] / "migrations/versions/20260919_ds0004_owner_storage_semantics.py"
        source = path.read_text(encoding="utf-8")
        self.assertIn("provider_column =", source)
        self.assertIn("AND adj_factor > 0 THEN 'complete'", source)
        self.assertIn("ELSE 'pending'", source)
        self.assertIn("ELSE 'unknown'", source)
        self.assertIn("WHEN factor_semantics IN ('same_day_identity_only','unknown')", source)
        self.assertIn("{relation}_adjustment_state_idx", source)
        self.assertIn("{relation}_adjustment_invalid_idx", source)
        self.assertIn("{relation}_semantic_invalid_idx", source)
        self.assertIn("Recompute every legacy bar row", source)
        self.assertIn("adjustment_state IS DISTINCT FROM CASE", source)


if __name__ == "__main__":
    unittest.main()
