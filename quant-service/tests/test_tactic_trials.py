import unittest
from decimal import Decimal

from app.tactic_trials import TACTICS, Tactic, day_result, evaluate_tactic


class TacticTrialTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
