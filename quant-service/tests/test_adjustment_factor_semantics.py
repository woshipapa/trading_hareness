import unittest
from decimal import Decimal
from pathlib import Path

from app.adjustment_factor_semantics import (
    COMPLETE_FACTOR_SEMANTICS,
    IDENTITY_FACTOR_SEMANTICS,
    adjustment_state,
    normalize_factor_row,
    research_factor_eligible,
)


class AdjustmentFactorSemanticsTests(unittest.TestCase):
    def test_missing_factor_is_absent_and_not_eligible(self):
        self.assertEqual(adjustment_state(None), "absent")
        self.assertFalse(research_factor_eligible(None))

    def test_identity_factor_is_retired(self):
        row = {"adj_factor": Decimal("1"), "provider": "longhuvip_composite",
               "factor_semantics": IDENTITY_FACTOR_SEMANTICS}
        self.assertEqual(adjustment_state(row), "retired")
        self.assertFalse(research_factor_eligible(row))

    def test_tushare_factor_is_complete(self):
        row = normalize_factor_row({"adj_factor": "1.25", "trade_date": "20260918"}, provider="tushare_primary")
        self.assertEqual(row["factor_semantics"], COMPLETE_FACTOR_SEMANTICS)
        self.assertEqual(adjustment_state(row), "complete")
        self.assertTrue(research_factor_eligible(row))

    def test_owner_longhu_qfq_factor_is_complete(self):
        row = normalize_factor_row({"adj_factor": "1.0878", "trade_date": "20260918"}, provider="longhu_qfq_derived")
        self.assertEqual(row["factor_semantics"], COMPLETE_FACTOR_SEMANTICS)
        self.assertEqual(adjustment_state(row), "complete")
        self.assertTrue(research_factor_eligible(row))

    def test_explicit_cumulative_label_cannot_bypass_unknown_provider(self):
        row = {"adj_factor": Decimal("1.25"), "provider": "akshare",
               "factor_semantics": COMPLETE_FACTOR_SEMANTICS}
        self.assertEqual(adjustment_state(row), "retired")
        self.assertFalse(research_factor_eligible(row))

    def test_non_tushare_factor_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_factor_row({"adj_factor": 1}, provider="longhuvip_composite")

    def test_non_tushare_legacy_factor_is_retired_not_complete(self):
        row = {"adj_factor": Decimal("1.25"), "provider": "longhuvip_composite"}
        self.assertEqual(adjustment_state(row), "retired")
        self.assertFalse(research_factor_eligible(row))

    def test_unknown_source_missing_factor_stays_pending(self):
        self.assertEqual(adjustment_state({"provider": "legacy_import"}), "pending")

    def test_research_sql_requires_persisted_semantics(self):
        app = Path(__file__).parents[1] / "app"
        for name in (
            "feature_snapshot_repository.py", "factor_sql_lab.py",
            "research_model_training_worker.py", "watchlist_main_wave.py",
            "watchlist_main_wave_v2.py", "watchlist_countertrend_rebound.py",
            "post_close_strategy_service.py", "factor_lab.py",
        ):
            source = (app / name).read_text(encoding="utf-8")
            self.assertTrue(
                "raw->>'factor_semantics'" in source
                or "persisted_factor_semantics_sql" in source,
                name,
            )
            self.assertTrue(
                "longhu_cq_preclose_qfq_v2" in source
                or "persisted_factor_semantics_sql" in source,
                name,
            )
            self.assertNotIn("COALESCE(factor.raw->>'factor_semantics','cumulative_tushare')", source, name)
            if name in {"watchlist_countertrend_rebound.py", "post_close_strategy_service.py"}:
                self.assertTrue(
                    "factor.provider=ANY(%s::text[])" in source
                    or "factor.provider = ANY(%s::text[])" in source,
                    name,
                )
            else:
                self.assertIn("longhu_qfq_derived", source, name)


if __name__ == "__main__":
    unittest.main()
