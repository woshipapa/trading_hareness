import unittest
from datetime import date, timedelta
from decimal import Decimal

from app.t1_settlement import (
    MAX_EXIT_ROLL_SESSIONS, ROUND_TRIP_COST, SettlementBar, market_window_return, settle,
)

D = Decimal


def bars(*specs, start=date(2026, 9, 1), symbol_limit=D("0.10")):
    """Each spec is (open, close) or a dict of overrides; limits from pre_close."""
    result, previous_close = [], D("10")
    for index, spec in enumerate(specs):
        values = dict(spec) if isinstance(spec, dict) else {"open": spec[0], "close": spec[1]}
        open_, close = D(str(values.get("open", previous_close))), D(str(values["close"]))
        pre_close = D(str(values.get("pre_close", previous_close)))
        result.append(SettlementBar(
            trading_date=start + timedelta(days=index), open=open_,
            high=D(str(values.get("high", max(open_, close)))), low=D(str(values.get("low", min(open_, close)))),
            close=close, pre_close=pre_close, adj_factor=D(str(values["adj"])) if "adj" in values else D("1"),
            is_suspended=bool(values.get("suspended")),
            limit_up=(pre_close * (1 + symbol_limit)).quantize(D("0.01")),
            limit_down=(pre_close * (1 - symbol_limit)).quantize(D("0.01")),
        ))
        previous_close = close
    return result


class T1SettlementTests(unittest.TestCase):
    def test_a_one_day_horizon_cannot_sell_the_day_it_bought(self):
        # Old rule: buy 10.0 open, sell 10.5 same-day close (+5%).  Under T+1
        # the earliest exit is the next session's close.
        result = settle(bars((10.0, 10.5), (10.4, 9.8)), symbol="600000.SH", direction=1, horizon_sessions=1)
        self.assertEqual(result["status"], "settled")
        self.assertEqual(result["sessions_held"], 2)
        self.assertEqual(result["exit_date"], date(2026, 9, 2))
        self.assertEqual(result["gross_return"], D("9.8") / D("10.0") - 1)

    def test_a_one_day_horizon_waits_for_the_next_session(self):
        result = settle(bars((10.0, 10.5)), symbol="600000.SH", direction=1, horizon_sessions=1)
        self.assertEqual(result["status"], "pending")

    def test_an_entry_opening_at_limit_up_is_never_settled(self):
        result = settle(bars((11.0, 11.0), (11.5, 11.8)), symbol="600000.SH", direction=1, horizon_sessions=2)
        self.assertEqual(result, {"status": "entry_unfillable", "reason": "entry_opened_at_limit_up",
                                  "entry_date": date(2026, 9, 1)})

    def test_a_suspended_entry_is_never_settled(self):
        result = settle(bars({"close": 10, "suspended": True}, (10, 10)), symbol="600000.SH", direction=1,
                        horizon_sessions=2)
        self.assertEqual(result["reason"], "entry_suspended")

    def test_an_exit_sealed_at_limit_down_rolls_to_the_next_sellable_close(self):
        # Session 2 closes at its limit-down (10 * 0.9 = 9.0): no one can sell
        # into it.  Session 3 falls again and trades at 8.5 - that is the exit.
        path = bars((10.0, 10.0), (9.0, 9.0), (8.2, 8.5))
        result = settle(path, symbol="600000.SH", direction=1, horizon_sessions=2)
        self.assertEqual(result["exit_date"], date(2026, 9, 3))
        self.assertEqual(result["exit_rolled_sessions"], 1)
        self.assertEqual(result["tradability"], "exit_rolled_past_blocked_session")
        self.assertEqual(result["gross_return"], D("8.5") / D("10.0") - 1)

    def test_a_roll_that_runs_out_of_data_stays_pending(self):
        result = settle(bars((10.0, 10.0), (9.0, 9.0)), symbol="600000.SH", direction=1, horizon_sessions=2)
        self.assertEqual(result["status"], "pending")

    def test_a_roll_is_bounded_and_flagged(self):
        specs = [(10.0, 10.0)]
        close = D("10.0")
        for _ in range(MAX_EXIT_ROLL_SESSIONS + 2):
            close = (close * D("0.9")).quantize(D("0.01"))
            specs.append({"open": close, "close": close})
        result = settle(bars(*specs), symbol="600000.SH", direction=1, horizon_sessions=2)
        self.assertEqual(result["tradability"], "exit_blocked_valued_at_close")
        self.assertEqual(result["exit_rolled_sessions"], MAX_EXIT_ROLL_SESSIONS)

    def test_an_ex_rights_gap_is_not_a_loss_on_adjusted_prices(self):
        # A 10-for-10 bonus halves the raw price; adj_factor doubles.
        path = bars({"open": 10.0, "close": 10.0, "adj": 1},
                    {"open": 5.1, "close": 5.2, "pre_close": 5.0, "adj": 2})
        result = settle(path, symbol="600000.SH", direction=1, horizon_sessions=2)
        self.assertEqual(result["price_basis"], "adj_factor_adjusted")
        self.assertEqual(result["gross_return"], D("5.2") * 2 / D("10.0") - 1)

    def test_a_missing_factor_falls_back_to_raw_and_says_so(self):
        path = bars((10.0, 10.0), (10.2, 10.3))
        path[1] = SettlementBar(**{**path[1].__dict__, "adj_factor": None})
        result = settle(path, symbol="600000.SH", direction=1, horizon_sessions=2)
        self.assertEqual(result["price_basis"], "unadjusted_missing_adj_factor")

    def test_net_return_subtracts_the_shared_round_trip_cost(self):
        result = settle(bars((10.0, 10.0), (10.2, 10.3)), symbol="600000.SH", direction=1, horizon_sessions=2)
        self.assertEqual(result["net_return"], result["gross_return"] - ROUND_TRIP_COST)
        self.assertGreater(ROUND_TRIP_COST, D("0.002"))

    def test_a_bearish_thesis_is_scored_symmetrically(self):
        result = settle(bars((10.0, 10.0), (9.6, 9.5)), symbol="600000.SH", direction=-1, horizon_sessions=2,
                        benchmark_return=lambda _entry, _exit: D("-0.02"))
        self.assertEqual(result["gross_return"], (D("9.5") / D("10.0") - 1) * -1)
        self.assertEqual(result["benchmark_return"], D("0.02"))
        self.assertEqual(result["excess_return"], result["gross_return"] - D("0.02"))

    def test_the_benchmark_compounds_entry_open_to_close_then_close_to_close(self):
        daily = {
            date(2026, 9, 1): {"open_to_close": D("0.01"), "close_to_close": D("0.02")},
            date(2026, 9, 2): {"open_to_close": D("0"), "close_to_close": D("-0.01")},
            date(2026, 9, 3): {"open_to_close": D("0"), "close_to_close": D("0.03")},
        }
        self.assertEqual(market_window_return(daily, date(2026, 9, 1), date(2026, 9, 2)),
                         D("1.01") * D("0.99") - 1)
        self.assertIsNone(market_window_return(daily, date(2026, 8, 31), date(2026, 9, 2)))

    def test_limits_fall_back_to_the_board_band_when_not_stored(self):
        path = [SettlementBar(trading_date=date(2026, 9, 1), open=D("12.0"), high=D("12.0"), low=D("12.0"),
                              close=D("12.0"), pre_close=D("10.0"), adj_factor=D("1"))]
        # ChiNext 20% band: 12.00 is the limit-up open.
        result = settle(path, symbol="300750.SZ", direction=1, horizon_sessions=2)
        self.assertEqual(result["reason"], "entry_opened_at_limit_up")


if __name__ == "__main__":
    unittest.main()
