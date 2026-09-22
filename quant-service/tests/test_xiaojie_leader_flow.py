import unittest

from app.xiaojie_leader_flow import MODEL_VERSION, evaluate_snapshot, research_alert_allowed


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

    #: A 潜龙 setup that satisfies all five evidence items and reads cool on
    #: every overheat input, so each test changes exactly one thing.
    QIANLONG_PASSING = {
        "ma_spread_min_10d_pct": 2.0, "consolidation_box_range_pct": 30.0,
        "breakout_confirmed": True, "daily_history_complete": True,
        "support_or_vwap_holds": True, "candidate_in_main_sector": True,
        "fundamental_pe": 25.0, "overhead_high_distance_pct": 0.0,
        "distance_from_ma20_pct": 5.0, "pre_signal_5d_return_pct": 2.0,
        "sector_day_return_pct": 0.5, "sector_net_inflow_rate_pct": 1.0,
        "stock_vs_sector_divergence_pct": 4.0,
    }

    def _qianlong_swing_snapshot(self, **overrides):
        # Falls through every more specific mode (no re-seal, no reverse-wrap
        # confirmation, no VWAP pullback, no right-side/icepoint/oversold/
        # supplement/ETF fields) to land on the 潜龙出海_swing catch-all.
        return self._snapshot(
            prior_one_word_board=False,
            limit_up_return_flow=False,
            breakout_or_reverse_wrap=True,
            **{**self.QIANLONG_PASSING, **overrides},
        )

    def test_qianlong_swing_with_no_overheat_flags_keeps_normal_position(self):
        result = evaluate_snapshot(self._qianlong_swing_snapshot())
        self.assertEqual(result["mode"], "潜龙出海_swing")
        self.assertEqual(result["decision"], "research_candidate")
        self.assertEqual(result["qianlong_swing_overheat"]["count"], 0)
        self.assertTrue(result["qianlong_evidence"]["passed"])
        self.assertEqual(result["position"]["target_fraction"], 0.10)

    def test_qianlong_swing_without_evidence_is_sent_under_a_red_warning(self):
        # v2 read every absent field as "no flag"; v3 treats absence as unknown
        # and says so on the reminder instead of silently passing it.
        bare = self._snapshot(prior_one_word_board=False, limit_up_return_flow=False,
                              breakout_or_reverse_wrap=True)
        result = evaluate_snapshot(bare)
        self.assertEqual(result["mode"], "潜龙出海_swing")
        self.assertEqual(result["decision"], "research_candidate")
        self.assertEqual(result["position"]["target_fraction"], 0.05)
        self.assertEqual(result["qianlong_warning"]["level"], "red")
        self.assertIn("qianlong_red_warning", result["risk_flags"])
        self.assertIn("qianlong_evidence_incomplete", result["risk_flags"])
        self.assertIn("qianlong_overheat_inputs_incomplete", result["risk_flags"])
        self.assertEqual(set(result["qianlong_evidence"]["missing"]),
                         {"ma_confluence", "breakout_volume", "pullback_support", "fundamental"})

    def test_qianlong_swing_one_overheat_flag_downgrades_to_high_risk_fraction(self):
        result = evaluate_snapshot(self._qianlong_swing_snapshot(distance_from_ma20_pct=20.0))
        self.assertEqual(result["mode"], "潜龙出海_swing")
        self.assertEqual(result["decision"], "research_candidate")
        self.assertEqual(result["qianlong_swing_overheat"]["count"], 1)
        self.assertIn("qianlong_swing_extended_above_ma20", result["risk_flags"])
        self.assertEqual(result["position"]["target_fraction"], 0.05)

    def test_qianlong_overheat_inputs_missing_are_not_a_clean_pass(self):
        result = evaluate_snapshot(self._qianlong_swing_snapshot(sector_net_inflow_rate_pct=None))
        self.assertEqual(result["decision"], "research_candidate")
        self.assertEqual(result["qianlong_swing_overheat"]["missing"], ["sector_net_inflow_rate_pct"])
        self.assertIn("qianlong_overheat_inputs_incomplete", result["risk_flags"])
        self.assertEqual(result["position"]["target_fraction"], 0.05)

    def test_a_loss_maker_is_sent_with_a_red_warning_naming_the_pe(self):
        # 跨境通: "形态理论上符合、基本面不行".
        result = evaluate_snapshot(self._qianlong_swing_snapshot(fundamental_pe=-25.76))
        self.assertEqual(result["decision"], "research_candidate")
        self.assertEqual(result["qianlong_evidence"]["failed"], ["fundamental"])
        self.assertIn("qianlong_evidence_failed", result["risk_flags"])
        self.assertEqual(result["qianlong_warning"]["reasons"], ["潜龙证据不成立：基本面兑现（PE -25.8，亏损）"])
        self.assertEqual(result["position"]["target_fraction"], 0.05)

    def test_convergence_without_a_volume_marker_k_is_refused(self):
        # 共进股份: "均线粘合、没有放量标志性 K".
        result = evaluate_snapshot(self._qianlong_swing_snapshot(breakout_confirmed=False))
        self.assertEqual(result["qianlong_warning"]["level"], "red")
        self.assertEqual(result["qianlong_evidence"]["evidence"]["breakout_volume"], False)
        self.assertEqual(result["qianlong_evidence"]["evidence"]["pullback_support"], False)

    def test_a_second_volume_reverse_wrap_is_a_marker_k(self):
        # 华海诚科: "二次放量反包买点".
        result = evaluate_snapshot(self._qianlong_swing_snapshot(
            breakout_confirmed=False, reverse_wrap_volume_confirmed=True))
        self.assertEqual(result["decision"], "research_candidate")
        self.assertEqual(result["qianlong_evidence"]["detail"]["breakout_volume"], "volume_reverse_wrap_today")

    def test_pullback_after_an_earlier_marker_k_must_hold_ma5_or_the_box_top(self):
        held = evaluate_snapshot(self._qianlong_swing_snapshot(
            breakout_confirmed=False, marker_k_sessions_ago=2,
            signed_distance_from_ma5_pct=-0.5, distance_from_box_top_pct=-3.0))
        self.assertIsNone(held["qianlong_warning"]["level"])
        lost = evaluate_snapshot(self._qianlong_swing_snapshot(
            breakout_confirmed=False, marker_k_sessions_ago=2,
            signed_distance_from_ma5_pct=-2.5, distance_from_box_top_pct=-4.0))
        self.assertEqual(lost["qianlong_warning"]["level"], "red")
        self.assertEqual(lost["qianlong_evidence"]["failed"], ["pullback_support"])
        stale = evaluate_snapshot(self._qianlong_swing_snapshot(
            breakout_confirmed=False, marker_k_sessions_ago=8))
        self.assertEqual(stale["qianlong_evidence"]["evidence"]["breakout_volume"], False)

    def test_no_convergence_and_a_wide_box_fails_the_first_evidence(self):
        result = evaluate_snapshot(self._qianlong_swing_snapshot(
            ma_spread_min_10d_pct=6.0, consolidation_box_range_pct=45.0))
        self.assertEqual(result["qianlong_evidence"]["failed"], ["ma_confluence"])
        boxed = evaluate_snapshot(self._qianlong_swing_snapshot(
            ma_spread_min_10d_pct=6.0, consolidation_box_range_pct=15.0))
        self.assertEqual(boxed["qianlong_evidence"]["detail"]["ma_confluence"], "box_range")

    def test_overhead_pressure_only_downgrades(self):
        result = evaluate_snapshot(self._qianlong_swing_snapshot(overhead_high_distance_pct=2.0))
        self.assertEqual(result["decision"], "research_candidate")
        self.assertIn("qianlong_overhead_pressure", result["risk_flags"])
        self.assertEqual(result["qianlong_warning"], {"level": "yellow", "reasons": ["上方前高压力：距60日高点2.0%"]})
        self.assertEqual(result["position"]["target_fraction"], 0.05)

    def test_blocking_can_be_restored_for_a_walk_forward_comparison(self):
        warned = self._qianlong_swing_snapshot(fundamental_pe=-3.0)
        self.assertEqual(evaluate_snapshot(warned)["decision"], "research_candidate")
        blocked = evaluate_snapshot(warned, {"qianlong_gates_block": True})
        self.assertEqual(blocked["decision"], "no_trade")
        self.assertEqual(blocked["position"]["target_fraction"], 0.0)
        # A yellow warning never blocks, even under the blocking policy.
        yellow = evaluate_snapshot(self._qianlong_swing_snapshot(distance_from_ma20_pct=20.0),
                                   {"qianlong_gates_block": True})
        self.assertEqual(yellow["decision"], "research_candidate")

    def test_three_overheat_flags_are_sent_under_a_red_warning_with_values(self):
        # 新华文轩 601811 on 2026-09-22 10:30: 40% above MA20, +27% in five
        # sessions, 文化传媒 +3.8% on an 8.7% net inflow rate.
        result = evaluate_snapshot(self._qianlong_swing_snapshot(
            distance_from_ma20_pct=40.3, pre_signal_5d_return_pct=27.1,
            sector_day_return_pct=3.833, sector_net_inflow_rate_pct=8.66,
            stock_vs_sector_divergence_pct=6.2,
        ))
        self.assertEqual(result["decision"], "research_candidate")
        self.assertEqual(result["position"]["target_fraction"], 0.05)
        self.assertEqual(result["qianlong_swing_overheat"]["count"], 3)
        self.assertEqual(result["qianlong_warning"]["level"], "red")
        self.assertEqual(result["qianlong_warning"]["reasons"],
                         ["过热3项：高于20日线40%、5日已涨27%、板块已热（涨+3.8%，净流入率+8.7%）"])

    def test_qianlong_swing_overheat_does_not_apply_outside_its_own_mode(self):
        result = evaluate_snapshot(self._snapshot(
            prior_one_word_board=False, limit_up_return_flow=False,
            leader_pullback_to_vwap=True, main_sector_present=True,
            distance_from_ma20_pct=50.0, pre_signal_5d_return_pct=50.0,
        ))
        self.assertEqual(result["mode"], "leader_pullback")
        self.assertEqual(result["qianlong_swing_overheat"], {"flags": [], "count": 0})
        self.assertEqual(result["position"]["target_fraction"], 0.20)

    def test_sealed_qianlong_candidate_is_allowed_as_research_alert(self):
        candidate = {
            "mode": "潜龙出海_swing",
            "evidence": {"board": {"sealed": True}},
        }
        self.assertTrue(research_alert_allowed(candidate))

    def test_sealed_non_qianlong_candidate_stays_out_of_alert_queue(self):
        candidate = {
            "mode": "right_side_breakout",
            "evidence": {"board": {"sealed": True}},
        }
        self.assertFalse(research_alert_allowed(candidate))


if __name__ == "__main__":
    unittest.main()
