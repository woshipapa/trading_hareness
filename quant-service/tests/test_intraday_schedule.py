"""Capacity and cadence guards shared by realtime monitor paths."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.intraday_schedule import (
    INTRADAY_WATCHLIST_MAX_SYMBOLS,
    intraday_watchlist_capacity,
    intraday_watchlist_max_symbols,
    seconds_until_slot,
)


class IntradayScheduleSlotTests(unittest.TestCase):
    def test_waits_for_the_next_slot_before_on_and_after_it(self) -> None:
        self.assertEqual(seconds_until_slot(49, 60, 50), 1)
        self.assertEqual(seconds_until_slot(50, 60, 50), 60)
        self.assertEqual(seconds_until_slot(50.5, 60, 50), 59.5)


class IntradayScheduleCapacityTests(unittest.TestCase):
    def test_watchlist_overflow_blocks_instead_of_silently_truncating(self) -> None:
        # The owner overlay may explicitly configure a larger pool.  This
        # regression asserts the audited default contract, so pass it
        # explicitly instead of inheriting the deployment environment.
        within = intraday_watchlist_capacity(INTRADAY_WATCHLIST_MAX_SYMBOLS, max_symbols=INTRADAY_WATCHLIST_MAX_SYMBOLS)
        overflow = intraday_watchlist_capacity(INTRADAY_WATCHLIST_MAX_SYMBOLS + 1, max_symbols=INTRADAY_WATCHLIST_MAX_SYMBOLS)

        self.assertFalse(within["blocked"])
        self.assertTrue(overflow["blocked"])
        self.assertEqual(overflow["max_symbols"], INTRADAY_WATCHLIST_MAX_SYMBOLS)
        self.assertIn("exceeds", str(overflow["reason"]))

    def test_expanded_pool_is_explicitly_configurable_but_bounded(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(intraday_watchlist_max_symbols(), 160)
        with patch.dict("os.environ", {"INTRADAY_WATCHLIST_MAX_SYMBOLS": "100"}):
            self.assertEqual(intraday_watchlist_max_symbols(), 100)
            self.assertFalse(intraday_watchlist_capacity(100)["blocked"])
            self.assertTrue(intraday_watchlist_capacity(101)["blocked"])
        with patch.dict("os.environ", {"INTRADAY_WATCHLIST_MAX_SYMBOLS": "1000"}):
            self.assertEqual(intraday_watchlist_max_symbols(), 200)


if __name__ == "__main__":
    unittest.main()
