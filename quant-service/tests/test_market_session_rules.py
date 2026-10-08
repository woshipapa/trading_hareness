"""The one session decision both session repositories now share."""

from __future__ import annotations

import unittest
from datetime import date

from app.market_session_rules import calendar_verdict, session_verdict, weekend_verdict


class SessionRuleTests(unittest.TestCase):
    def test_calendar_rows(self):
        self.assertEqual(calendar_verdict(None), (False, "SSE trade calendar has no entry for today; fail closed"))
        self.assertEqual(calendar_verdict({"is_open": False}), (False, "SSE trade calendar marks today closed"))
        self.assertEqual(calendar_verdict({"is_open": True}), (True, "SSE trade calendar marks today open"))

    def test_weekends_close_without_a_query(self):
        self.assertEqual(weekend_verdict(date(2026, 10, 10))[0], False)
        self.assertIsNone(weekend_verdict(date(2026, 10, 9)))

    def test_both_the_clock_and_the_calendar_must_be_open(self):
        self.assertEqual(session_verdict((False, "before the open"), (True, "open")), (False, "before the open"))
        self.assertEqual(session_verdict((True, "continuous auction"), (False, "closed day")), (False, "closed day"))
        self.assertEqual(session_verdict((True, "continuous auction"), (True, "open")), (True, "continuous auction"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
