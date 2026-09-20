import unittest

from app.research_prices import adjusted_bars, adjusted_value, research_price_eligible


class ResearchPriceContractTests(unittest.TestCase):
    def test_legacy_in_memory_row_remains_compatible(self):
        self.assertTrue(research_price_eligible({"close": 10, "adj_factor": 1}))
        self.assertEqual(adjusted_value({"close": 10, "adj_factor": 1}), 10)

    def test_pending_or_identity_rows_fail_closed(self):
        for row in (
            {"close": 10, "adj_factor": 1, "adjustment_state": "pending"},
            {"close": 10, "adj_factor": 1, "adjustment_state": "complete", "factor_semantics": "same_day_identity_only"},
            {"close": 10, "adj_factor": 1, "adjustment_state": "complete", "factor_semantics": "cumulative_tushare", "factor_provider": "akshare"},
        ):
            self.assertFalse(research_price_eligible(row))
            self.assertIsNone(adjusted_value(row))
            self.assertEqual(adjusted_bars([row]), (None, ["adj_factor_missing"]))

    def test_complete_cumulative_tushare_row_is_eligible(self):
        row = {
            "close": 10, "adj_factor": 1.2,
            "adjustment_state": "complete", "factor_semantics": "cumulative_tushare",
            "factor_provider": "tushare_primary",
        }
        self.assertTrue(research_price_eligible(row))
        self.assertEqual(adjusted_value(row), 12)


if __name__ == "__main__":
    unittest.main()
