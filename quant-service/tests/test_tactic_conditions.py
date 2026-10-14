from __future__ import annotations

import unittest
from datetime import date, timedelta

from app.tactic_conditions import (
    CONDITIONS, build_auction_row, build_day_context, build_trial_row, evaluate_conditions, evaluate_ladder_roles,
    history_features,
)


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

    def test_condition_registry_and_close_evaluation(self):
        close = {
            "symbol": "000001.SZ", "sector": "Theme", "theme_count": 10, "boards": 3,
            "first_seal_time": "15:00:00", "seal_money": 10_000_000, "vr": 3,
            "turnover_vs_max250": 1, "sealed_from_open": False, "reopened": True,
            "runup_20": 0.4, "turnover": 10_000_000_000, "close_vs_high60": 0.1,
            "main_net": -1, "risk_rank": 80, "cost_share_below_close": 0.95,
            "cost_share_0_30_above": 0.3,
        }
        other = {**close, "symbol": "000002.SZ", "boards": 1, "first_seal_time": "15:30:00"}
        evaluated = evaluate_conditions([close, other], {
            "boards_3plus_count": 6, "limit_up_count": 21, "market_turnover": 2.1e12,
            "market_turnover_median60": 2.0e12, "prev_top_board_sealed_again": False,
        }, "close")
        flags = set(evaluated["rows"][0]["flags"])
        self.assertIn("theme_big", flags)
        self.assertIn("theme_leader_earliest", flags)
        self.assertIn("theme_leader_top_board", flags)
        self.assertIn("market_top_board", flags)
        self.assertIn("seal_late", flags)
        self.assertIn("reopened", flags)
        self.assertEqual(set(evaluated["day"]["flags"]), {"env_ladder_ok", "env_turnover_2p1", "env_turnover_above_median60", "env_top_board_failed"})
        self.assertEqual({condition.key for condition in CONDITIONS}, {
            "theme_big", "theme_7plus", "theme_lone", "theme_leader_earliest", "theme_leader_top_board",
            "market_top_board", "board_2_3", "board_4plus", "board_6plus", "seal_small", "seal_large",
            "volume_heavy", "volume_shrunk", "amount_record_250", "seal_late", "seal_early",
            "sealed_from_open", "reopened", "runup_40", "turnover_10bn", "above_high60", "main_net_out",
            "risk_top_fifth", "catch_up_first_board", "cost_above_band", "cost_overhead_heavy",
            "env_ladder_ok", "env_turnover_2p1", "env_turnover_above_median60", "env_top_board_failed",
            "opens_at_limit", "gap_down_2", "gap_up_5_not_sealed", "gap_3_to_8", "gap_0_to_5",
            "auction_amount_top5", "auction_burst", "board_gap_below_typical",
            "tier_first", "front_row", "kawei_leader", "leader_switch", "assist", "back_row",
            "sole_high_board", "ladder_full", "weak_to_strong", "divergence_to_consensus",
            "consensus_to_divergence", "rebound_board",
        })

        unknown = {**close, "theme_count": None, "vr": None, "first_seal_time": None}
        unknown_result = evaluate_conditions([unknown], {
            "boards_3plus_count": None, "limit_up_count": None, "market_turnover": None,
            "market_turnover_median60": None, "prev_top_board_sealed_again": None,
        }, "close")
        self.assertIn("theme_big", unknown_result["rows"][0]["unknown"])
        self.assertIn("volume_heavy", unknown_result["rows"][0]["unknown"])
        self.assertIn("env_ladder_ok", unknown_result["day"]["unknown"])

    def test_auction_builder_and_prev_condition_evaluation(self):
        previous = {"complete": True, "flags": ["theme_big"], "unknown": ["seal_small"], "turnover": 1_000_000}
        pool = [
            {"symbol": "000001.SZ", "name": "A", "open": 10.6, "gap": 0.06, "opens_at_limit": False,
             "bucket": "5-8", "risk_rank": 80, "boards": 2, "sealed_from_open": False,
             "amount": 500_000, "volume": 10, "unmatched": 2},
            {"symbol": "000002.SZ", "amount": 100_000},
            {"symbol": "000003.SZ", "amount": None},
        ]
        row = build_auction_row(pool[0], pool, previous)
        evaluated = evaluate_conditions([row], {}, "auction")["rows"][0]
        self.assertEqual(row["gap_bucket"], "5-8")
        self.assertEqual(row["board_bucket"], "2")
        self.assertEqual(row["auction_amount_rank"], 1)
        self.assertIn("gap_up_5_not_sealed", evaluated["flags"])
        self.assertIn("auction_amount_top5", evaluated["flags"])
        self.assertIn("prev.theme_big", evaluated["flags"])
        self.assertIn("prev.seal_small", evaluated["unknown"])
        self.assertNotIn("prev.theme_7plus", evaluated["flags"])

        missing = build_auction_row({**pool[0], "amount": None}, pool, None)
        missing_result = evaluate_conditions([missing], {}, "auction")["rows"][0]
        self.assertIn("auction_amount_top5", missing_result["unknown"])
        self.assertIn("prev.theme_big", missing_result["unknown"])

    def test_condition_claim_metadata_and_theme_absence(self):
        by_key = {condition.key: condition for condition in CONDITIONS}
        self.assertEqual(by_key["board_4plus"].claim_outcome, "big_loss")
        self.assertEqual(by_key["board_4plus"].claim, 1)
        self.assertEqual(by_key["assist"].claim_outcome, "seal")
        self.assertEqual(by_key["assist"].claim, -1)
        self.assertEqual(by_key["amount_record_250"].history["status"], "against")
        self.assertTrue(by_key["amount_record_250"].history["text"].startswith("against，见 "))
        self.assertEqual(by_key["tier_first"].history["text"], "+2.51% / +2.18%；+19.6 / +18.1 points")

        decoded = {
            "symbol": "000001.SZ", "name": "A", "sector": "", "first_seal_time": 1_767_231_000,
            "seal_money": 1.0, "seal_max": 1.0, "main_net": 1.0, "turnover": 1.0,
            "float_cap": 1.0, "boards": 2.0, "reopened": 0.0,
            "bar": {"high": 11.0, "low": 11.0, "open": 10.0, "pre_close": 10.0},
        }
        features = {field: None for field in ("vr", "turnover_vs_max250", "runup_20", "close_vs_high60", "cost_share_below_close", "cost_share_0_30_above")}
        row = build_trial_row(decoded, features, {}, [decoded])
        self.assertIsNone(row["theme_count"])
        evaluated = evaluate_conditions([row], {}, "close")
        self.assertIn("theme_big", evaluated["rows"][0]["unknown"])
        self.assertIn("theme_leader_top_board", evaluated["rows"][0]["unknown"])

    def test_prior_leader_rules_and_rebound_require_valid_readings(self):
        def row(symbol, sector, boards, seal, **values):
            return {"symbol": symbol, "sector": sector, "boards": boards, "first_seal_time": seal,
                    "one_word": False, "reopened": False, "vr": 1.0, "seal_money": 50_000_000, **values}

        today = [row("A", "Theme", 2, "09:00:00"), row("B", "Theme", 1, "10:00:00")]
        yesterday_low_leader = {"complete": True, "rows": [row("A", "Theme", 1, "14:00:00")]}
        today_roles = evaluate_ladder_roles(today, [yesterday_low_leader, yesterday_low_leader, yesterday_low_leader])
        self.assertNotIn("kawei_leader", today_roles[0]["flags"])
        self.assertNotIn("kawei_leader", today_roles[0]["unknown"])

        rebound = [row("A", "Theme", 1, "09:00:00")]
        incomplete = evaluate_ladder_roles(rebound, [None, {"complete": True, "rows": [row("A", "Theme", 2, "09:00:00")]}, {"complete": True, "rows": []}])
        self.assertIn("rebound_board", incomplete[0]["unknown"])

    def test_ladder_roles_on_two_themes_over_four_sessions(self):
        def row(symbol, sector, boards, seal, **values):
            return {"symbol": symbol, "sector": sector, "boards": boards, "first_seal_time": seal,
                    "one_word": False, "reopened": False, "vr": 1.0, "seal_money": 50_000_000, **values}

        today = [
            row("A1", "A", 3, "09:30:00", one_word=True), row("A2", "A", 2, "08:30:00"),
            row("A3", "A", 1, "09:45:00", one_word=True), row("A4", "A", 1, "10:00:00"),
            row("B2", "B", 2, "08:00:00"), row("B3", "B", 2, "09:15:00"), row("B4", "B", 1, "10:30:00"),
        ]
        yesterday = [
            row("A1", "A", 3, "14:00:00"), row("A2", "A", 2, "13:30:00", reopened=True),
            row("A3", "A", 1, "10:00:00", reopened=True, vr=2.0), row("A4", "A", 1, "10:30:00", one_word=True),
            row("B1", "B", 3, "09:00:00"), row("B0", "B", 1, "10:00:00"),
        ]
        two_back = [row("B4", "B", 2, "09:00:00")]
        three_back = [row("B4", "B", 2, "09:00:00")]
        readings = [
            {"trade_date": "2026-10-12", "complete": True, "rows": yesterday},
            {"trade_date": "2026-10-09", "complete": True, "rows": two_back},
            {"trade_date": "2026-10-08", "complete": True, "rows": three_back},
        ]

        evaluated = evaluate_ladder_roles(today, readings)
        by_symbol = {item["symbol"]: set(item["flags"]) for item in evaluated}
        self.assertIn("tier_first", by_symbol["B2"])
        self.assertIn("front_row", by_symbol["A2"])
        self.assertIn("kawei_leader", by_symbol["A2"])
        self.assertIn("leader_switch", by_symbol["B2"])
        self.assertIn("assist", by_symbol["A3"])
        self.assertIn("back_row", by_symbol["A4"])
        self.assertIn("sole_high_board", by_symbol["A1"])
        self.assertIn("ladder_full", by_symbol["A1"])
        self.assertIn("weak_to_strong", by_symbol["A2"])
        self.assertIn("divergence_to_consensus", by_symbol["A3"])
        self.assertIn("consensus_to_divergence", by_symbol["A4"])
        self.assertIn("rebound_board", by_symbol["B4"])
        self.assertNotIn("back_row", by_symbol["A2"])

        unknown = evaluate_ladder_roles(today, [None, {"complete": True, "rows": two_back}, None])
        unknown_by_symbol = {item["symbol"]: set(item["unknown"]) for item in unknown}
        self.assertIn("kawei_leader", unknown_by_symbol["A2"])
        self.assertIn("weak_to_strong", unknown_by_symbol["A2"])


if __name__ == "__main__":
    unittest.main()
