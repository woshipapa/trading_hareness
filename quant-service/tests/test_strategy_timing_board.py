"""The timing board exposes an idea and its reason without turning it into an order."""

from __future__ import annotations

import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.platform.strategy_registry import STRATEGY_CONTRACTS
from app.strategy_timing_board import build_timing_board, research_candidate, timing_plans


CN = ZoneInfo("Asia/Shanghai")


class StrategyTimingBoardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pick = {
            "symbol": "600001.SH", "name": "核心甲", "rank": 1, "score": 88,
            "auction": {"pct_change": 4.2, "turnover": 12_000_000},
            "resonance": 2, "themes": [{"label": "机器人"}],
            "limit_review": {"theme": "机器人", "reason": "昨日涨停"},
            "entry_signals": [{"model": "leader-v1"}], "latest_pct": 7.5,
        }

    def test_auction_candidate_is_explicit_and_missing_fields_are_kept(self):
        candidate = research_candidate("dragon_leader_research", {**self.pick, "limit_review": None}, phase="auction")
        self.assertEqual((candidate["status"], candidate["research_action"], candidate["phase"]),
                         ("research_candidate", "buy_candidate", "auction"))
        self.assertEqual(candidate["entry_mode"], "auction_candidate")
        self.assertTrue(candidate["selection_reasons"])
        self.assertIn("limit_review", candidate["missing_evidence"])
        self.assertTrue(candidate["research_only"])
        self.assertEqual(candidate["live_effect"], "none")

    def test_intraday_uses_current_reference_and_reason(self):
        candidate = research_candidate("xiaojie_leader_flow", {**self.pick, "price": 10.8}, phase="intraday")
        self.assertEqual(candidate["entry_mode"], "intraday_confirmation")
        self.assertEqual(candidate["entry_reference"], 10.8)
        self.assertIn("承接、回流", candidate["selection_reasons"][0])
        self.assertTrue(any("竞价涨跌幅" in reason for reason in candidate["selection_reasons"]))

    def test_board_lists_every_registered_strategy_even_without_a_pick(self):
        board = build_timing_board(
            [{"strategy_key": "launch_radar", "contract_key": "launch_radar", "name": "启动雷达",
              "picks": [self.pick]}],
            observed_at=datetime(2026, 10, 15, 9, 22, tzinfo=CN), trade_date=date(2026, 10, 15),
        )
        self.assertEqual(board["active_phase"], "auction")
        self.assertEqual(len(board["strategies"]), len(STRATEGY_CONTRACTS))
        launch = next(row for row in board["strategies"] if row["strategy_key"] == "launch_radar")
        self.assertEqual(launch["status"], "candidates_available")
        self.assertEqual(launch["candidates"][0]["symbol"], "600001.SH")
        missing = next(row for row in board["strategies"] if row["strategy_key"] == "dragon_leader_research")
        self.assertEqual(missing["status"], "waiting_for_candidates")
        self.assertEqual(board["evening_review"]["settlement_source"], "quant.strategy_daily_candidate_outcomes")

    def test_all_contracts_have_at_least_one_timing_plan(self):
        self.assertTrue(all(timing_plans(key) and timing_plans(key)[0]["phase"] for key in STRATEGY_CONTRACTS))

    def test_historical_trade_date_is_closed_even_before_todays_open(self):
        board = build_timing_board([], observed_at=datetime(2026, 10, 16, 0, 18, tzinfo=CN),
                                   trade_date=date(2026, 10, 15))
        self.assertEqual(board["active_phase"], "post_close")


if __name__ == "__main__":
    unittest.main()
