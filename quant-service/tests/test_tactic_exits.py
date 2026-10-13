from __future__ import annotations

import unittest
from datetime import date, timedelta
from decimal import Decimal

from app.tactic_exits import EXITS, evaluate_pick
from app.t1_settlement import SettlementBar

D = Decimal


def path(*specs, factor=D("1")):
    result = []
    previous = D("10")
    for index, spec in enumerate(specs):
        values = dict(spec)
        open_ = D(str(values.get("open", previous)))
        close = D(str(values.get("close", open_)))
        pre = D(str(values.get("pre_close", previous)))
        result.append(SettlementBar(
            trading_date=date(2026, 10, 1) + timedelta(days=index), open=open_,
            high=D(str(values.get("high", max(open_, close)))), low=D(str(values.get("low", min(open_, close)))),
            close=close, pre_close=pre, adj_factor=values.get("adj_factor", factor),
            is_suspended=values.get("suspended", False), limit_up=pre * D("1.1"), limit_down=pre * D("0.9"),
        ))
        previous = close
    return result


class TacticExitTests(unittest.TestCase):
    def test_gap_over_target_fills_at_open_and_brackets_differ(self):
        bars = path({"open": 10, "close": 10}, {"open": 10, "close": 10}, {"open": 11.2, "close": 9, "high": 11.5, "low": 8.8})

        result = evaluate_pick("600000.SH", "close", bars, D("10"))

        self.assertEqual(result["entries"]["buy_open"]["tp5"]["field"], "open")
        self.assertEqual(result["entries"]["buy_open"]["tp5"]["raw_return"], D("11.2") / D("10") - 1)
        self.assertEqual(result["entries"]["buy_open"]["tp5_sl3_stop_first"]["price"], D("10") * D("0.97"))
        self.assertEqual(result["entries"]["buy_open"]["tp5_sl3_target_first"]["price"], D("10") * D("1.05"))

    def test_target_and_stop_use_adjusted_basis_through_ex_rights(self):
        bars = path({"open": 10, "close": 10, "adj_factor": D("1")},
                    {"open": 5, "close": 5, "pre_close": 5, "high": 5.3, "low": 4.8, "adj_factor": D("2")},
                    {"open": 10.8, "close": 10.8, "adj_factor": D("1")})

        result = evaluate_pick("600000.SH", "close", bars, D("10"))

        self.assertEqual(result["entries"]["buy_open"]["tp5"]["price_basis"], "adj_factor_adjusted")
        self.assertEqual(result["entries"]["buy_open"]["tp5"]["status"], "settled")

    def test_down_limit_s1_open_rolls_open_rule_to_close(self):
        bars = path({"open": 10, "close": 10}, {"open": 9, "close": 9.5}, {"open": 9.5, "close": 9.8})

        result = evaluate_pick("600000.SH", "close", bars, D("10"))

        self.assertEqual(result["entries"]["held"]["open"]["field"], "close")
        self.assertEqual(result["entries"]["held"]["open"]["exit_index"], 1)

    def test_unfillable_buy_entry_and_missing_bar(self):
        blocked = path({"open": 10, "close": 10}, {"open": 11, "close": 11})
        result = evaluate_pick("600000.SH", "close", blocked, D("10"))
        self.assertEqual(result["entries"]["buy_open"]["close"]["status"], "no_trade")
        missing = evaluate_pick("600000.SH", "close", [], D("10"))
        self.assertEqual(missing["entries"]["held"]["close"]["status"], "missing_bar")

    def test_state_outcomes_and_room_measures(self):
        bars = path({"open": 10, "close": 10}, {"open": 10, "close": 11, "high": 12, "low": 9})

        result = evaluate_pick("600000.SH", "close", bars, D("10"))

        self.assertTrue(result["seal"])
        self.assertFalse(result["big_loss"])
        self.assertEqual(result["s1_high"], D("12") / D("10") - 1)
        self.assertEqual(result["s1_low"], D("9") / D("10") - 1)
        self.assertEqual(result["s3_high"], result["s1_high"])


if __name__ == "__main__":
    unittest.main()
