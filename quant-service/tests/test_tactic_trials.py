import json
import math
import unittest
import unittest.mock
from datetime import date, timedelta
from decimal import Decimal

from app.research_trials import bh_q_values
from pathlib import Path

from app.tactic_trials import (
    TACTICS, History, PairHistory, Tactic, daily_summary, day_digest, day_pairs, day_result, evaluate_tactic,
)
from tactic_trial_days import fingerprint, record_outcomes, rounded, settled_days

REFERENCE = Path(__file__).parent / "fixtures" / "tactic_fold_reference_b04be9b9.json"


def fold(days, tactics=TACTICS):
    """What the evening job does: each day's digest and pairs through JSON, folded oldest first."""
    history, pairs = History(tactics), PairHistory()
    for day in days:
        history.add(json.loads(json.dumps(day_digest(day, tactics))))
        pairs.add(json.loads(json.dumps(day_pairs(day))))
    return history, pairs


def summary(days, previous=None):
    history, pairs = fold(days)
    latest = {day["slot"]: day for day in days}   # the newest day of each slot: the days are oldest first
    return daily_summary(history, pairs, list(latest.values()), previous, {slot: len(day["rows"]) for slot, day in latest.items()})


class TacticTrialTests(unittest.TestCase):
    def test_a_flag_whose_condition_left_the_registry_is_shown_by_its_key(self):
        from app.tactic_trials import _condition_name, registry_fingerprint
        from app.tactic_conditions import CONDITIONS
        self.assertIn("gone_condition", _condition_name("gone_condition"))
        self.assertTrue(_condition_name("prev.gone_condition").startswith("昨日"))
        with unittest.mock.patch("app.tactic_trials.CONDITIONS", CONDITIONS[:-1]):
            changed = registry_fingerprint()
        self.assertNotEqual(changed, registry_fingerprint(), "a removed condition makes the stored digests and pairs stale")

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
        records = fold(days, (tactic,))[0].records()
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
        records = fold(days, (tactic,))[0].records()
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
        switches = fold(days)[0].switch_records()
        self.assertEqual(switches["env_ladder_ok"]["true_days"], 10)
        self.assertEqual(switches["env_ladder_ok"]["false_days"], 10)
        self.assertIsNotNone(switches["env_ladder_ok"]["t"])
        self.assertIsNone(fold(days[:5])[0].switch_records()["env_ladder_ok"]["t"])

        pair_days = []
        for index in range(20):
            day = self._day((date(2026, 2, 1) + timedelta(days=index)).isoformat())
            day["rows"][0]["flags"] = ["theme_big", "theme_7plus"]
            pair_days.append(day)
        pairs = fold(pair_days)[1].result()["close"]
        self.assertGreaterEqual(pairs["pairs_reaching_minimum"], 1)
        self.assertGreater(pairs["pairs_examined"], pairs["pairs_reaching_minimum"])
        self.assertTrue(pairs["mined"])

    def test_summary_sections_pick_day_label_changes_and_json(self):
        days = settled_days(3)
        result = summary(days)
        lines = result["lines"]
        self.assertEqual([line for line in lines if line[:2] in {"一、", "二、", "三、", "四、", "五、", "六、", "七、"}],
                         ["一、当日", "二、战法", "三、案例", "四、当日条件", "五、战绩", "六、卖点", "七、两两组合"])
        self.assertEqual(lines[1], "收盘：今日样本 12 行；下面是选股日 2026-08-22 的 12 行在其后的表现（累计 36 行）")
        self.assertEqual(result["day"]["sample_sizes"]["auction"],
                         {"pick_day": "2026-08-22", "rows": 12, "cumulative_rows": 36, "today_rows": 12})
        self.assertFalse([line for line in lines if "prev." in line or "only." in line], "no raw key reaches the text")
        changed = summary(days, {"records": {"leader_hold": {"label": "against"}}})["label_changes"]
        self.assertEqual([item for item in changed if item["key"] == "leader_hold"],
                         [{"key": "leader_hold", "previous": "against", "current": "collecting"}])
        self.assertEqual(set(result["sell_points"]["close"]), {"day", "cumulative", "exit_tables"})
        self.assertEqual(list(result["sell_points"]["auction"]["cumulative"]["gap_bucket"]), ["0-5", "5+", "<0"], "buckets in one order")
        json.dumps(result)

    def test_the_fold_gives_what_the_computation_over_the_full_history_gave(self):
        """The reference was computed by the code before the fold (commit b04be9b9), on the same days."""
        reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
        days = settled_days()
        self.assertEqual(len(days), reference["days"])
        history, pairs = fold(days)
        records = history.records()
        self.assertEqual(rounded({tactic.key: {"label": records[tactic.key]["label"], "q": records[tactic.key]["q"],
                                               **records[tactic.key]["outcomes"][tactic.primary]} for tactic in TACTICS}, 12),
                         reference["primary"])
        for tactic in TACTICS:
            self.assertEqual(set(records[tactic.key]["outcomes"]), set(record_outcomes(tactic) or records[tactic.key]["outcomes"]))
        self.assertEqual(fingerprint({key: value["outcomes"] for key, value in records.items()}), reference["records_sha256"])
        self.assertEqual(rounded(history.switch_records(), 12), reference["switch_records"])
        latest = {day["slot"]: day for day in days}
        sell = daily_summary(history, pairs, list(latest.values()))["sell_points"]
        self.assertEqual(rounded({slot: sell[slot]["cumulative"]["all"] for slot in sell}, 12), reference["sell_cumulative_all"])
        self.assertEqual(fingerprint(sell), reference["sell_points_sha256"])
        mined = pairs.result()
        for slot in ("close", "auction"):
            for side in ("highest", "lowest"):
                for pair in mined[slot][side]:
                    self.assertEqual([name.startswith("昨日") for name in pair.pop("names")],
                                     [key.startswith("prev.") for key in pair["conditions"]],
                                     "a condition read from the session before is named as such")
        self.assertEqual(rounded(mined, 12), reference["pairs"])
        self.assertEqual(reference["picking_combinations"], 20, "every registered combination picks on these days")

    def test_a_digest_of_another_registry_is_not_silently_folded(self):
        tactic = Tactic("trial", "试验", "close", "hold", "", all_of=("pick",), cell=("board_bucket",), entry="held", exit="close", primary="held:close")
        digest = day_digest(self._day("2026-10-13"), (tactic,))
        self.assertLess(len(json.dumps(digest)), 60_000, "a day digest stays small")
        with self.assertRaises(KeyError):
            History().add(digest)

if __name__ == "__main__":
    unittest.main()
