from __future__ import annotations

from datetime import date
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from app.recommendation_generation import CURRENT_CLOSE_MISSING_FLAG, current_close_available, generate


class RecommendationCloseContractTests(unittest.TestCase):
    def test_v4_close_gate_accepts_only_the_requested_session(self) -> None:
        as_of = date(2026, 9, 18)
        self.assertTrue(current_close_available({"market_data_date": "2026-09-18"}, as_of))
        self.assertTrue(current_close_available({"market_data_date": as_of}, as_of))
        self.assertFalse(current_close_available({"market_data_date": "2026-09-17"}, as_of))
        self.assertFalse(current_close_available({"market_data_date": None}, as_of))

    def test_close_missing_flag_is_explicit_and_stable(self) -> None:
        self.assertEqual(CURRENT_CLOSE_MISSING_FLAG, "current_close_missing")

    def test_generation_keeps_prior_close_as_watch_only(self) -> None:
        connection = MagicMock()
        database = MagicMock()
        database.transaction.return_value.__enter__.return_value = connection
        request = SimpleNamespace(
            as_of_date=date(2026, 9, 18), universe_key="core", limit=10, horizon_days=5,
        )
        payload = generate(
            request,
            cn_today=lambda: request.as_of_date,
            build_feature_snapshot=lambda *_: {
                "snapshot_key": "snapshot-1", "market_regime": "neutral",
                "items": [{
                    "symbol": "000001.SZ", "quality_flags": [],
                    "features": {
                        "market_data_date": "2026-09-17", "bar_count": 60,
                        "close": 10.0, "sma_20": 9.0, "return_5": 0.05,
                        "return_20": 0.10,
                    },
                }],
            },
            analyst_execution_context=lambda *_: {"execution_eligible": False, "max_live_weight": 0.0},
            ablation_scores=lambda **_: {"applied_score": 65.0, "market_only_score": 65.0, "analyst_shadow_score": 65.0},
            number=lambda value, default=None: default if value is None else float(value),
            db=database, model_version="test-model", feature_version="multi-source-feature-v4",
            json_safe=lambda value: value,
        )
        recommendation = payload["recommendations"][0]
        self.assertEqual(recommendation["decision"], "watch")
        self.assertIn(CURRENT_CLOSE_MISSING_FLAG, recommendation["flags"])



if __name__ == "__main__":
    unittest.main()
