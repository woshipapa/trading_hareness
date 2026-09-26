from __future__ import annotations

from datetime import datetime, timezone
import unittest

from app.sentiment_cycle import classify_stage
from app.ten_day_leader_rotation_intraday_research import (
    evaluate_intraday_rotation_candidates,
    intraday_rotation_due,
    select_intraday_rotation_slice,
)
from app.ten_day_leader_rotation_research import cycle_context_from_sentiment

# A prior session the platform's own classifier reads as fermenting: few broken
# boards and a high promotion rate, short of a climax ladder.
FERMENTING_READING = {"broken_rate": 0.10, "promotion_rate": 0.50, "max_board_height": 4, "high_board_count": 2}


class TenDayLeaderRotationIntradayResearchTests(unittest.TestCase):
    def test_rotation_due_covers_the_normal_scanner_phase(self) -> None:
        self.assertTrue(intraday_rotation_due(datetime(2026, 8, 24, 2, 0, 5, tzinfo=timezone.utc)))
        self.assertFalse(intraday_rotation_due(datetime(2026, 8, 24, 2, 0, 35, tzinfo=timezone.utc)))

    def test_rotation_is_bounded_and_evaluation_uses_live_not_prior_return(self) -> None:
        candidates = [
            {"symbol": f"600{index:03d}.SH", "board": "main", "board_rank": index,
             "ten_day_return_pct": 10, "current_return_pct": 99}
            for index in range(1, 31)
        ]
        observed_at = datetime(2026, 8, 24, 2, 0, tzinfo=timezone.utc)
        selected, offset = select_intraday_rotation_slice(candidates, observed_at, limit=7)

        self.assertEqual(len(selected), 7)
        self.assertGreaterEqual(offset, 0)
        observations = evaluate_intraday_rotation_candidates(
            run={"strategy_available_at": datetime(2026, 8, 21, 10, tzinfo=timezone.utc)},
            candidates=[selected[0]], observed_at=observed_at,
            quotes={selected[0]["symbol"]: {"pct_change": 4.0, "price": 10}},
            minute_features={}, peer_contexts={},
            market_contexts={selected[0]["symbol"]: {"market_state": "broad_risk_on"}},
            quote_source=lambda _: "tencent_all_a_snapshot",
            cycle_context=cycle_context_from_sentiment({"stage": classify_stage(FERMENTING_READING)}),
        )

        self.assertEqual(observations[0]["shadow_state"], "ranked_but_not_expanding")
        self.assertFalse(observations[0]["shadow_eligible"])
        self.assertFalse(observations[0]["decision_eligible"])

    def test_complete_causal_inputs_can_only_create_shadow_eligibility(self) -> None:
        candidate = {"symbol": "600001.SH", "board": "main", "board_rank": 1,
                     "ten_day_return_pct": 12, "current_return_pct": 5}
        observation = evaluate_intraday_rotation_candidates(
            run={"strategy_available_at": datetime(2026, 8, 21, 10, tzinfo=timezone.utc)},
            candidates=[candidate], observed_at=datetime(2026, 8, 24, 2, tzinfo=timezone.utc),
            quotes={"600001.SH": {"pct_change": 6.0, "price": 10}},
            minute_features={"600001.SH": {"above_vwap_pct": 0.2, "return_3m_pct": 0.8, "minute_volume_multiple": 1.7}},
            peer_contexts={"600001.SH": {"exact_membership_groups": [{"sector_key": "x"}],
                                            "available_peer_count": 2, "confirming_peer_count": 2,
                                            "confirming_breadth": 1.0}},
            market_contexts={"600001.SH": {"market_state": "broad_risk_on"}},
            quote_source=lambda _: "tencent_all_a_snapshot",
            cycle_context=cycle_context_from_sentiment(
                {"stage": classify_stage(FERMENTING_READING), "trading_date": "2026-08-21"},
            ),
        )[0]

        self.assertEqual(observation["shadow_state"], "confirmed_coordination")
        self.assertTrue(observation["shadow_eligible"])
        self.assertFalse(observation["decision_eligible"])
        self.assertEqual(observation["evidence"]["cycle_state"], "attack_accelerating")
        self.assertEqual(observation["evidence"]["cycle_source"]["sentiment_stage"], "fermenting")
        self.assertEqual(observation["evidence"]["cycle_source"]["sentiment_trading_date"], "2026-08-21")

    def _complete_inputs(self, **overrides):
        candidate = {"symbol": "600001.SH", "board": "main", "board_rank": 1,
                     "ten_day_return_pct": 12, "current_return_pct": 5}
        return dict(
            run={"strategy_available_at": datetime(2026, 8, 21, 10, tzinfo=timezone.utc)},
            candidates=[candidate], observed_at=datetime(2026, 8, 24, 2, tzinfo=timezone.utc),
            quotes={"600001.SH": {"pct_change": 6.0, "price": 10}},
            minute_features={"600001.SH": {"above_vwap_pct": 0.2, "return_3m_pct": 0.8, "minute_volume_multiple": 1.7}},
            peer_contexts={"600001.SH": {"exact_membership_groups": [{"sector_key": "x"}],
                                            "available_peer_count": 2, "confirming_peer_count": 2,
                                            "confirming_breadth": 1.0}},
            market_contexts={"600001.SH": {"market_state": "broad_risk_on"}},
            quote_source=lambda _: "tencent_all_a_snapshot",
            **overrides,
        )

    def test_the_board_flow_regime_is_never_read_as_a_cycle_state(self) -> None:
        # Regression: the rule used to read market_state, whose values
        # (broad_risk_on, rotation_*, mixed_or_neutral) never name a workbook
        # cycle state, so every candidate was blocked.  Without a sentiment
        # cycle it now fails closed and says why.
        observation = evaluate_intraday_rotation_candidates(**self._complete_inputs())[0]
        self.assertEqual(observation["shadow_state"], "cycle_risk_blocked")
        self.assertEqual(observation["evidence"]["cycle_source"]["reason"], "sentiment_cycle_not_supplied")

    def test_risk_and_unmapped_sentiment_stages_block(self) -> None:
        for stage, flag in (("icepoint", "cycle_ice_point"), ("ebbing", "cycle_retreat"),
                            ("climax", "cycle_climax"), ("mixed", "cycle_mixed")):
            observation = evaluate_intraday_rotation_candidates(
                **self._complete_inputs(cycle_context=cycle_context_from_sentiment({"stage": stage})),
            )[0]
            self.assertEqual(observation["shadow_state"], "cycle_risk_blocked", stage)
            self.assertIn(flag, observation["risk_flags"])

    def test_a_missing_prior_session_reading_fails_closed(self) -> None:
        observation = evaluate_intraday_rotation_candidates(
            **self._complete_inputs(cycle_context=cycle_context_from_sentiment(None)),
        )[0]
        self.assertEqual(observation["shadow_state"], "cycle_risk_blocked")
        self.assertIn("cycle_unavailable", observation["risk_flags"])
        self.assertEqual(observation["evidence"]["cycle_source"]["reason"], "prior_session_sentiment_missing")


if __name__ == "__main__":
    unittest.main()
