import unittest

from app.longhu_multifactor_shadow import MODEL_VERSION, build_features, score_candidate


class LonghuMultifactorShadowTests(unittest.TestCase):
    def test_complete_positive_evidence_creates_only_a_shadow_candidate(self) -> None:
        features = build_features({
            "quote_return_pct": 5.0,
            "minute_momentum_pct": 1.8,
            "volume_ratio": 2.0,
            "order_book_imbalance": 0.7,
            "large_order_net_ratio": 0.6,
            "board_relative_pct": 3.0,
            "auction_premium_pct": 4.0,
        })
        result = score_candidate(features, stale_seconds=5)
        self.assertEqual(result["model_version"], MODEL_VERSION)
        self.assertEqual(result["state"], "shadow_candidate")
        self.assertEqual(result["factor_coverage"], 1.0)
        self.assertTrue(result["research_only"])
        self.assertEqual(result["live_effect"], "none")

    def test_missing_or_stale_evidence_fails_closed(self) -> None:
        result = score_candidate(
            {"minute_momentum": 0.8, "order_book_balance": 0.9},
            stale_seconds=45,
        )
        self.assertEqual(result["state"], "blocked")
        self.assertIn("stale_evidence", result["risk_flags"])
        self.assertIn("insufficient_factor_coverage", result["risk_flags"])


if __name__ == "__main__":
    unittest.main()
