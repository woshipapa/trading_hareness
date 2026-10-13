import json
import math
import unittest
from datetime import date, timedelta
from decimal import Decimal

from app.research_trials import bh_q_values
from app.tactic_trials import (
    TACTICS, Tactic, build_daily_summary, build_pairs, build_records, build_sell_points,
    build_switch_records, day_result, evaluate_tactic,
)


class TacticTrialTests(unittest.TestCase):
    def _day(self, day, *, slot="close", true=True, two_rows=True, day_flag=None):
        rows = [
            {"symbol": "P", "name": "Pick", "boards": 2, "board_bucket": "2", "gap_bucket": "0-5",
             "flags": ["pick", "a", "b"] if true else [], "unknown": []},
        ]
        if two_rows:
            rows.append({"symbol": "C", "name": "Compare", "boards": 2, "board_bucket": "2", "gap_bucket": "0-5", "flags": [], "unknown": []})
        outcomes = {}
        for row in rows:
            value = Decimal("0.10") if row["symbol"] == "P" else Decimal("0.00")
            outcomes[row["symbol"]] = {
                "entries": {"held": {"close": {"raw_return": value, "net_return": value, "status": "settled"}}},
                "seal": row["symbol"] == "P", "big_loss": False, "open_to_close": Decimal("0.01"),
                "s1_high": Decimal("0.10"), "s3_high": Decimal("0.20"), "s1_low": Decimal("-0.02"),
            }
        return {"trade_date": day, "slot": slot, "day": {"flags": [day_flag] if day_flag else [], "unknown": []}, "rows": rows, "outcomes": outcomes}

    def test_three_valued_all_none_and_at_least(self):
        all_tactic = Tactic("all", "", "close", "hold", "", all_of=("a", "b"))
        none_tactic = Tactic("none", "", "close", "hold", "", none_of=("a", "b"))
        at_least = Tactic("at", "", "close", "hold", "", at_least=(2, ("a", "b", "c")))
        day = {"flags": [], "unknown": []}
        self.assertIsNone(evaluate_tactic(all_tactic, {"flags": ["a"], "unknown": ["b"]}, day))
        self.assertFalse(evaluate_tactic(all_tactic, {"flags": ["a"], "unknown": []}, day))
        self.assertIsNone(evaluate_tactic(none_tactic, {"flags": [], "unknown": ["a"]}, day))
        self.assertFalse(evaluate_tactic(none_tactic, {"flags": ["a"], "unknown": []}, day))
        self.assertTrue(evaluate_tactic(at_least, {"flags": ["a", "b"], "unknown": []}, day))
        self.assertFalse(evaluate_tactic(at_least, {"flags": ["a"], "unknown": []}, day))
        self.assertIsNone(evaluate_tactic(at_least, {"flags": ["a"], "unknown": ["b"]}, day))

    def test_every_combination_picks_its_definition_not_a_near_miss(self):
        combinations = TACTICS[:20]
        for tactic in combinations:
            with self.subTest(tactic=tactic.key):
                flags = list(tactic.all_of)
                if tactic.at_least:
                    flags.extend(tactic.at_least[1][: tactic.at_least[0]])
                day_flags = list(tactic.day_conditions)
                row = {"flags": flags, "unknown": [], "board_bucket": "2", "gap_bucket": "0-5"}
                if tactic.slot == "auction":
                    row["prev"] = {}
                day = {"flags": day_flags, "unknown": []}
                self.assertTrue(evaluate_tactic(tactic, row, day))
                near_miss = {**row, "flags": [key for key in flags if key not in tactic.all_of[:1]]}
                if tactic.all_of:
                    self.assertFalse(evaluate_tactic(tactic, near_miss, day))
                elif tactic.at_least:
                    near_miss = {**row, "flags": flags[1:]}
                    self.assertFalse(evaluate_tactic(tactic, near_miss, day))
                elif tactic.none_of:
                    blocked = {**row, "flags": [*flags, tactic.none_of[0]]}
                    self.assertFalse(evaluate_tactic(tactic, blocked, day))
                else:
                    self.assertIsNot(evaluate_tactic(tactic, row, day), False)

    def test_prev_condition_is_unknown_without_previous_row(self):
        tactic = next(item for item in TACTICS if item.key == "leader_moderate_gap")
        row = {"flags": ["gap_0_to_5"], "unknown": [], "board_bucket": "2", "gap_bucket": "0-5"}
        self.assertIsNone(evaluate_tactic(tactic, row, {"flags": [], "unknown": []}))

    def test_day_result_cells_missing_comparison_and_no_trade(self):
        tactic = Tactic(
            "trial", "", "close", "hold", "", all_of=("pick",), cell=("board_bucket",),
            entry="held", exit="close", primary="held:close",
        )
        rows = [
            {"symbol": "P1", "flags": ["pick"], "unknown": [], "board_bucket": "1"},
            {"symbol": "C1", "flags": [], "unknown": [], "board_bucket": "1"},
            {"symbol": "P2", "flags": ["pick"], "unknown": [], "board_bucket": "2"},
            {"symbol": "U", "flags": [], "unknown": ["pick"], "board_bucket": "1"},
        ]
        settled = {"status": "settled", "raw_return": Decimal("0.10"), "net_return": Decimal("0.10")}
        comparison = {"status": "settled", "raw_return": Decimal("0.05"), "net_return": Decimal("0.05")}
        no_trade = {"status": "no_trade", "raw_return": None, "net_return": None}
        outcomes = {
            "P1": {"entries": {"held": {"close": settled}}},
            "C1": {"entries": {"held": {"close": comparison}}},
            "P2": {"entries": {"held": {"close": no_trade}}},
        }
        result = day_result(tactic, rows, {"flags": [], "unknown": []}, outcomes)
        self.assertEqual(result["counts"], {
            "picks": 2, "picks_without_comparison_row": 1, "rows_unknown": 1,
            "picks_no_trade": 1, "picks_not_resolved": 0,
        })
        self.assertEqual(result["primary"]["pick_mean"], Decimal("0.10"))
        self.assertEqual(result["primary"]["comparison_mean"], Decimal("0.05"))
        self.assertEqual(result["primary"]["difference"], Decimal("0.05"))
        self.assertEqual(result["primary"]["pick_values_missing"], 1)

    def test_registry_has_single_condition_tactics_and_plan_entries(self):
        self.assertEqual(len(TACTICS), 20 + 46)
        self.assertEqual(next(t for t in TACTICS if t.key == "teacher_auction_entry").exit, "must_rise")
        self.assertEqual(next(t for t in TACTICS if t.key == "amount_top5_big_theme").cell, ("board_bucket", "gap_bucket"))
        self.assertEqual(next(t for t in TACTICS if t.key == "only.market_top_board").primary, "seal")

    def test_records_split_label_and_q_value(self):
        tactic = Tactic("trial", "试验", "close", "hold", "", all_of=("pick",), cell=("board_bucket",), entry="held", exit="close", primary="held:close", registered_on=date(2026, 10, 13))
        days = [self._day((date(2026, 10, 10) + timedelta(days=index)).isoformat()) for index in range(2)]
        days.extend(self._day((date(2026, 10, 13) + timedelta(days=index)).isoformat()) for index in range(21))
        records = build_records(days, (tactic,))
        item = records["trial"]
        self.assertEqual(item["outcomes"]["held:close"]["replay"]["in_sample"], True)
        self.assertEqual(item["outcomes"]["held:close"]["replay"]["days_with_picks"], 2)
        self.assertEqual(item["outcomes"]["held:close"]["forward"]["picks"], 21)
        self.assertEqual(item["label"], "collecting")

        days.extend(self._day((date(2026, 11, 3) + timedelta(days=index)).isoformat()) for index in range(39))
        for index, day in enumerate(days[2:], 1):
            value = Decimal("0.10") + Decimal(index) / Decimal("1000")
            day["outcomes"]["P"]["entries"]["held"]["close"]["raw_return"] = value
            day["outcomes"]["P"]["entries"]["held"]["close"]["net_return"] = value
        records = build_records(days, (tactic,))
        forward = records["trial"]["outcomes"]["held:close"]["forward"]
        self.assertEqual(records["trial"]["label"], "supports")
        expected_q = bh_q_values({"trial": math.erfc(abs(forward["t"]) / math.sqrt(2))})["trial"]
        self.assertAlmostEqual(records["trial"]["q"], expected_q)

    def test_switch_records_and_pair_minimum(self):
        days = []
        for index in range(20):
            day = self._day((date(2026, 1, 1) + timedelta(days=index)).isoformat(), day_flag="env_ladder_ok" if index < 10 else None)
            if index >= 10:
                value = Decimal("-0.10") - Decimal(index - 10) / Decimal("1000")
                day["outcomes"]["P"]["entries"]["held"]["close"]["raw_return"] = value
                day["outcomes"]["P"]["entries"]["held"]["close"]["net_return"] = value
            else:
                value = Decimal("0.10") + Decimal(index) / Decimal("1000")
                day["outcomes"]["P"]["entries"]["held"]["close"]["raw_return"] = value
                day["outcomes"]["P"]["entries"]["held"]["close"]["net_return"] = value
            days.append(day)
        switches = build_switch_records(days)
        self.assertEqual(switches["env_ladder_ok"]["true_days"], 10)
        self.assertEqual(switches["env_ladder_ok"]["false_days"], 10)
        self.assertIsNotNone(switches["env_ladder_ok"]["t"])
        self.assertIsNone(build_switch_records(days[:5])["env_ladder_ok"]["t"])

        pair_days = []
        for index in range(20):
            day = self._day((date(2026, 2, 1) + timedelta(days=index)).isoformat())
            day["rows"][0]["flags"] = ["theme_big", "theme_7plus"]
            pair_days.append(day)
        pairs = build_pairs(pair_days)["close"]
        self.assertGreaterEqual(pairs["pairs_reaching_minimum"], 1)
        self.assertGreater(pairs["pairs_examined"], pairs["pairs_reaching_minimum"])
        self.assertTrue(pairs["mined"])

    def test_summary_lines_label_changes_sell_points_and_json(self):
        days = [self._day("2026-10-13"), self._day("2026-10-13", slot="auction")]
        summary = build_daily_summary(days)
        self.assertEqual(summary["lines"][0:8], [
            "一、当日", "收盘：2行（累计2行）", "收盘 二板：2只，再封板率 +50.00%；累计 +50.00%",
            "收盘日条件：无数据", "竞价：2行（累计2行）", "竞价 二板：2只，再封板率 +50.00%；累计 +50.00%",
            "竞价日条件：无数据", "二、战法",
        ])
        self.assertEqual(summary["lines"][7:9], [
            "二、战法",
            "未选中：龙头持有、二三板接力、远端放量清除、缩量加速、梯队环境接力、最高板断板后、弱板两项、梯队竞速胜者、卡位持有、助攻不接力、低位补涨、一字后首次打开、反包风险、卡位高开、梯队胜者温和高开、龙头温和高开、老师竞价入场、强题材竞价前五、弱板高开、弱板低开修复",
        ])
        self.assertEqual(summary["lines"][67:70], [
            "六、卖点", "收盘：", "已持有·收盘卖：当日 +5.00%，累计 +5.00%，首日高点 +10.00%",
        ])
        self.assertEqual(build_daily_summary(days, {"records": {"leader_hold": {"label": "against"}}})["label_changes"][0]["key"], "leader_hold")
        self.assertIn("cumulative", summary["sell_points"]["close"])
        json.dumps(summary)


if __name__ == "__main__":
    unittest.main()
