from __future__ import annotations

import unittest
from datetime import date, timedelta

from app.tactic_conditions import build_day_context, build_trial_row, history_features


class TacticConditionsTests(unittest.TestCase):
    def test_history_features_adjust_ex_rights_and_decay_costs(self):
        start = date(2026, 1, 1)
        window = []
        for index in range(60):
            factor = 2.0 if index in (30, 40) else 1.0
            window.append({
                "trading_date": start + timedelta(days=index), "close": 45.0 if index in (30, 40) else 90.0,
                "high": 90.0 / factor, "volume": 10.0, "amount": 10.0, "adj_factor": factor,
            })
        window.append({
            "trading_date": start + timedelta(days=60), "close": 100.0, "high": 100.0,
            "volume": 10.0, "amount": 20.0, "adj_factor": 1.0,
        })

        features = history_features(window)

        self.assertEqual(window[30]["close"] * window[30]["adj_factor"], 90.0)
        self.assertAlmostEqual(features["turnover_vs_max250"], 2.0)
        self.assertAlmostEqual(features["runup_20"], 100.0 / 90.0 - 1)
        self.assertAlmostEqual(features["close_vs_high60"], 100.0 / 90.0 - 1)
        self.assertAlmostEqual(features["cost_share_below_close"], 1.0)
        self.assertAlmostEqual(features["cost_share_0_30_above"], 0.0)
        self.assertEqual(features["history_basis"], "adjusted")

        raw_window = [dict(row) for row in window]
        raw_window[10]["adj_factor"] = None
        raw_features = history_features(raw_window)
        self.assertEqual(raw_features["history_basis"], "unadjusted_missing_adj_factor")
        self.assertAlmostEqual(raw_features["runup_20"], 100.0 / 45.0 - 1)

    def test_history_features_returns_none_for_short_windows(self):
        window = [{"close": 10.0, "high": 11.0, "volume": 10.0, "amount": 10.0, "adj_factor": 1.0} for _ in range(10)]

        self.assertEqual(history_features(window), {
            "vr": None, "turnover_vs_max250": None, "runup_20": None, "close_vs_high60": None,
            "cost_share_below_close": None, "cost_share_0_30_above": None,
            "history_basis": "adjusted",
        })

    def test_trial_row_counts_theme_and_maps_fields(self):
        decoded = {
            "symbol": "000001.SZ", "name": "A", "sector": "Theme", "first_seal_time": 1_767_231_000,
            "seal_money": 20_000_000.0, "seal_max": 30_000_000.0, "main_net": 4_000_000.0,
            "turnover": 100_000_000.0, "float_cap": 500_000_000.0, "boards": 3.0, "reopened": 1.0,
            "bar": {"high": 11.0, "low": 11.0, "open": 10.5, "pre_close": 10.0},
        }
        other = {**decoded, "symbol": "000002.SZ", "boards": 1.0, "bar": {"high": 11.0, "low": 10.0}}
        features = {
            "vr": 2.5, "turnover_vs_max250": 1.2, "runup_20": 0.3, "close_vs_high60": 0.1,
            "cost_share_below_close": 0.8, "cost_share_0_30_above": 0.2,
        }
        risk = {"vr": 99.0, "gap": 0.99, "risk_rank": 88.0}

        row = build_trial_row(decoded, features, risk, [decoded, other])

        self.assertEqual(row, {
            "symbol": "000001.SZ", "name": "A", "sector": "Theme", "boards": 3.0, "board_bucket": "3+",
            "first_seal_time": "09:30:00", "seal_money": 20_000_000.0, "seal_max": 30_000_000.0,
            "main_net": 4_000_000.0, "turnover": 100_000_000.0, "float_cap": 500_000_000.0,
            "reopened": 1.0, "sealed_from_open": True, "one_word": True, "vr": 2.5, "gap": 10.5 / 10.0 - 1,
            "risk_rank": 88.0, "theme_count": 2, **features,
        })

    def test_day_context(self):
        rows = [{"boards": 3}, {"boards": 2}, {"boards": 4}]

        self.assertEqual(build_day_context(rows, 2.1e12, 1.9e12, False), {
            "limit_up_count": 3, "boards_3plus_count": 2, "market_turnover": 2.1e12,
            "market_turnover_median60": 1.9e12, "prev_top_board_sealed_again": False,
        })


if __name__ == "__main__":
    unittest.main()
