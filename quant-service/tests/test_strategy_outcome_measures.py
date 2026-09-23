"""One definition of "how did this signal do", shared by every strategy."""

from __future__ import annotations

import unittest

from app.ashare_reality import round_trip_cost_pct
from app.strategy_outcome_measures import (
    SEALED_TOLERANCE, entry_evaluable, excess, locked_at, measure, returns,
)

COST = float(round_trip_cost_pct())


class EvaluabilityTests(unittest.TestCase):
    def test_a_price_at_the_limit_is_locked(self):
        self.assertIs(locked_at(11.0, 11.0), True)
        self.assertIs(locked_at(11.0 - SEALED_TOLERANCE / 2, 11.0), True)
        self.assertIs(locked_at(10.5, 11.0), False)

    def test_without_a_limit_price_nothing_is_claimed(self):
        self.assertIsNone(locked_at(11.0, None))
        self.assertIsNone(locked_at(None, 11.0))

    def test_the_scans_own_reading_wins_over_the_price_comparison(self):
        # The board was sealed even though the last print was a cent below.
        self.assertFalse(entry_evaluable(10.99, limit_up=11.0, flagged_sealed=True))
        self.assertTrue(entry_evaluable(11.0, limit_up=11.0, flagged_sealed=False))

    def test_an_archive_without_either_fact_is_treated_as_evaluable(self):
        self.assertTrue(entry_evaluable(10.5))


class ReturnTests(unittest.TestCase):
    def test_each_holding_period_is_charged_one_round_trip_not_one_per_day(self):
        measured = returns(10.0, session_close=10.5, next_open=10.6, next_close=11.0)
        self.assertAlmostEqual(measured["session_return_pct"], 5.0, places=4)
        self.assertAlmostEqual(measured["entry_to_next_close_pct"], 10.0, places=4)
        self.assertAlmostEqual(measured["net_session_return_pct"], 5.0 - COST, places=4)
        self.assertAlmostEqual(measured["net_next_open_to_close_pct"],
                               (11.0 / 10.6 - 1) * 100 - COST, places=4)

    def test_the_next_day_return_never_uses_the_entry_price(self):
        # This is the only number an account could have earned when the flagged
        # price was unavailable, so it must not inherit the entry.
        measured = returns(999.0, next_open=10.0, next_close=10.5)
        self.assertAlmostEqual(measured["next_open_to_close_pct"], 5.0, places=4)

    def test_missing_bars_leave_their_columns_empty_rather_than_zero(self):
        measured = returns(10.0)
        self.assertIsNone(measured["session_return_pct"])
        self.assertIsNone(measured["net_next_open_to_close_pct"])

    def test_excess_is_the_move_minus_the_days_median(self):
        self.assertAlmostEqual(excess(5.0, 1.2), 3.8, places=4)
        self.assertIsNone(excess(None, 1.2))
        self.assertIsNone(excess(5.0, None))


class MeasureTests(unittest.TestCase):
    def test_a_sealed_entry_is_marked_unevaluable_but_still_measured(self):
        result = measure({"price": 11.0, "sealed": True}, {"close": 11.0, "limit_up_price": 11.0})
        self.assertIs(result["evaluable"], False)
        self.assertEqual(result["reason"], "sealed_at_entry")
        # The numbers are still there; it is the scoring that excludes them.
        self.assertAlmostEqual(result["session_return_pct"], 0.0, places=4)

    def test_a_next_open_at_the_limit_is_flagged_as_unbuyable_too(self):
        result = measure({"price": 10.0}, {"close": 10.5, "limit_up_price": 11.0},
                         {"open": 11.55, "close": 11.6, "limit_up_price": 11.55})
        self.assertIs(result["next_open_locked"], True)

    def test_no_entry_is_not_an_outcome(self):
        self.assertEqual(measure(None, {"close": 10.0})["reason"], "no_entry")
        self.assertIsNone(measure({"price": None}, {"close": 10.0})["evaluable"])


if __name__ == "__main__":
    unittest.main()
