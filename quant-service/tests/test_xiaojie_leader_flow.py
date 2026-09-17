import unittest

from app.xiaojie_leader_flow import MODEL_VERSION, evaluate_snapshot


class XiaojieLeaderFlowTests(unittest.TestCase):
    def _snapshot(self, **overrides):
        snapshot = {
            "index_above_support": True,
            "index_volume_ratio": 1.25,
            "breadth_up_count": 3200,
            "breadth_down_count": 1200,
            "main_sector_present": True,
            "sector_strength_percentile": 0.92,
            "candidate_strength_rank": 1,
            "is_back_row": False,
            "turnover_rate": 8.5,
            "volume_ratio": 1.6,
            "prior_one_word_board": True,
            "limit_up_return_flow": True,
            "re_seal_confirmed": True,
            "intraday_above_vwap": True,
            "profit_cushion_pct": 0.08,
        }
        snapshot.update(overrides)
        return snapshot

    def test_one_word_return_flow_is_a_high_risk_research_candidate(self):
        result = evaluate_snapshot(self._snapshot())
        self.assertEqual(result["model_version"], MODEL_VERSION)
        self.assertEqual(result["mode"], "one_word_return_flow")
        self.assertEqual(result["decision"], "research_candidate")
        self.assertEqual(result["position"]["target_fraction"], 0.05)
        self.assertIn("high_risk_mode", result["risk_flags"])
        self.assertEqual(result["live_effect"], "none")

    def test_missing_market_evidence_fails_closed(self):
        result = evaluate_snapshot(self._snapshot(breadth_up_count=None))
        self.assertEqual(result["decision"], "no_trade")
        self.assertIn("insufficient_market_evidence", result["risk_flags"])

    def test_back_row_is_not_chased_even_when_market_is_good(self):
        result = evaluate_snapshot(self._snapshot(is_back_row=True, prior_one_word_board=False))
        self.assertEqual(result["decision"], "no_trade")
        self.assertIn("back_row_no_chase", result["risk_flags"])

    def test_ma5_break_without_recovery_reduces_half(self):
        result = evaluate_snapshot(self._snapshot(
            prior_one_word_board=False,
            limit_up_return_flow=False,
            reverse_wrap_confirmed=True,
            ma5_break_duration_minutes=20,
            ma5_recovered=False,
        ))
        self.assertEqual(result["exit"]["action"], "reduce_half")
        self.assertIn("ma5_break_unrecovered", result["exit"]["codes"])

    def test_futures_and_stock_both_rising_blocks_chase(self):
        result = evaluate_snapshot(self._snapshot(futures_stock_both_rising=True))
        self.assertEqual(result["decision"], "no_trade")
        self.assertIn("cross_asset_chase_risk", result["risk_flags"])

    def test_icepoint_is_only_a_small_left_side_trial_with_profit_cushion(self):
        result = evaluate_snapshot(self._snapshot(
            prior_one_word_board=False,
            limit_up_return_flow=False,
            icepoint=True,
            left_side_signal=True,
            distance_from_ma5_pct=4,
            profit_cushion_pct=0.08,
        ))
        self.assertEqual(result["mode"], "icepoint_left_trial")
        self.assertEqual(result["position"]["target_fraction"], 0.05)
        self.assertTrue(result["position"]["staged_entry"])

    def test_etf_trend_does_not_require_stock_leader_rank(self):
        result = evaluate_snapshot(self._snapshot(
            prior_one_word_board=False,
            limit_up_return_flow=False,
            is_etf=True,
            trend_support_holds=True,
            candidate_strength_rank=None,
        ))
        self.assertEqual(result["mode"], "etf_trend")
        self.assertEqual(result["decision"], "research_candidate")

    def test_long_term_dca_policy_is_exposed_as_research_metadata(self):
        result = evaluate_snapshot(self._snapshot())
        self.assertEqual(result["portfolio_policy"]["long_term_dca"]["parts_min"], 10)
        self.assertEqual(result["portfolio_policy"]["long_term_dca"]["buy_on_drawdown_pct"], [5.0, 10.0])

    def _qianlong_swing_snapshot(self, **overrides):
        # Falls through every more specific mode (no re-seal, no reverse-wrap
        # confirmation, no VWAP pullback, no right-side/icepoint/oversold/
        # supplement/ETF fields) to land on the 潜龙出海_swing catch-all.
        return self._snapshot(
            prior_one_word_board=False,
            limit_up_return_flow=False,
            breakout_or_reverse_wrap=True,
            **overrides,
        )

    def test_qianlong_swing_with_no_overheat_flags_keeps_normal_position(self):
        result = evaluate_snapshot(self._qianlong_swing_snapshot(
            distance_from_ma20_pct=5.0, pre_signal_5d_return_pct=2.0,
            sector_day_return_pct=0.5, sector_net_inflow_rate_pct=1.0,
            stock_vs_sector_divergence_pct=4.0,
        ))
        self.assertEqual(result["mode"], "潜龙出海_swing")
        self.assertEqual(result["decision"], "research_candidate")
        self.assertEqual(result["qianlong_swing_overheat"]["count"], 0)
        self.assertEqual(result["position"]["target_fraction"], 0.10)

    def test_qianlong_swing_fields_absent_do_not_change_existing_behavior(self):
        result = evaluate_snapshot(self._qianlong_swing_snapshot())
        self.assertEqual(result["mode"], "潜龙出海_swing")
        self.assertEqual(result["decision"], "research_candidate")
        self.assertEqual(result["qianlong_swing_overheat"], {"flags": [], "count": 0})
        self.assertEqual(result["position"]["target_fraction"], 0.10)

    def test_qianlong_swing_one_overheat_flag_downgrades_to_high_risk_fraction(self):
        result = evaluate_snapshot(self._qianlong_swing_snapshot(distance_from_ma20_pct=20.0))
        self.assertEqual(result["mode"], "潜龙出海_swing")
        self.assertEqual(result["decision"], "research_candidate")
        self.assertEqual(result["qianlong_swing_overheat"]["count"], 1)
        self.assertIn("qianlong_swing_extended_above_ma20", result["risk_flags"])
        self.assertEqual(result["position"]["target_fraction"], 0.05)

    def test_qianlong_swing_three_overheat_flags_blocks_entirely(self):
        result = evaluate_snapshot(self._qianlong_swing_snapshot(
            distance_from_ma20_pct=20.0, pre_signal_5d_return_pct=15.0,
            sector_day_return_pct=3.0, sector_net_inflow_rate_pct=5.0,
            stock_vs_sector_divergence_pct=0.0,
        ))
        self.assertEqual(result["decision"], "no_trade")
        self.assertEqual(result["position"]["target_fraction"], 0.0)
        self.assertGreaterEqual(result["qianlong_swing_overheat"]["count"], 3)

    def test_qianlong_swing_overheat_does_not_apply_outside_its_own_mode(self):
        result = evaluate_snapshot(self._snapshot(
            prior_one_word_board=False, limit_up_return_flow=False,
            leader_pullback_to_vwap=True, main_sector_present=True,
            distance_from_ma20_pct=50.0, pre_signal_5d_return_pct=50.0,
        ))
        self.assertEqual(result["mode"], "leader_pullback")
        self.assertEqual(result["qianlong_swing_overheat"], {"flags": [], "count": 0})
        self.assertEqual(result["position"]["target_fraction"], 0.20)


if __name__ == "__main__":
    unittest.main()
