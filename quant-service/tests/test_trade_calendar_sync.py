"""The forward exchange calendar from Fuyao, accepted only where it agrees with what we hold."""

from __future__ import annotations

import asyncio
import unittest
from datetime import date, timedelta

from app.trade_calendar_sync import extract_trading_days, plan_forward_rows, sync_forward_calendar

HOLIDAYS = {date(2026, 10, d) for d in range(1, 9)} | {date(2026, 9, 25), date(2027, 1, 1)}


def sessions(first: date, last: date) -> list[date]:
    day, found = first, []
    while day <= last:
        if day.weekday() < 5 and day not in HOLIDAYS:
            found.append(day)
        day += timedelta(days=1)
    return found


def held_calendar(first: date, last: date) -> dict[date, bool]:
    open_days = set(sessions(first, last))
    return {first + timedelta(days=n): (first + timedelta(days=n)) in open_days for n in range((last - first).days + 1)}


class ExtractionTests(unittest.TestCase):
    def test_finds_the_dates_wherever_the_vendor_nests_them(self):
        days = ["2026-10-09", "2026-10-12"]
        self.assertEqual(extract_trading_days({"items": days}), [date(2026, 10, 9), date(2026, 10, 12)])
        self.assertEqual(extract_trading_days({"result": {"list": ["20261009", "20261012"]}}),
                         [date(2026, 10, 9), date(2026, 10, 12)])
        self.assertEqual(extract_trading_days({"rows": [{"trade_date": "2026-10-09", "is_open": 1},
                                                        {"trade_date": "2026-10-10", "is_open": 0}]}),
                         [date(2026, 10, 9)])

    def test_a_response_without_dates_yields_nothing(self):
        self.assertEqual(extract_trading_days({"items": ["soon", "later"]}), [])
        self.assertEqual(extract_trading_days({"count": 3}), [])


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.held = held_calendar(date(2026, 1, 1), date(2026, 12, 31))

    def test_agreeing_list_adds_only_dates_after_the_held_calendar(self):
        plan = plan_forward_rows(sessions(date(2026, 1, 1), date(2027, 6, 30)), self.held, today=date(2026, 10, 9))
        self.assertEqual(plan.status, "ready")
        self.assertEqual(plan.rows[0][0], date(2027, 1, 1))
        self.assertEqual(plan.rows[0][1], False, "a listed holiday stays closed")
        self.assertEqual(plan.rows[0][2], date(2026, 12, 31), "pretrade is the last held session")
        first_open = next(row for row in plan.rows if row[1])
        self.assertEqual(first_open[0], date(2027, 1, 4))
        self.assertEqual(plan.rows[-1][0], date(2027, 6, 30))
        self.assertGreater(plan.agreed_dates, 300)

    def test_any_disagreement_with_a_held_date_blocks_everything(self):
        listed = [day for day in sessions(date(2026, 1, 1), date(2027, 6, 30)) if day != date(2026, 10, 9)]
        plan = plan_forward_rows(listed, self.held, today=date(2026, 10, 9))
        self.assertEqual(plan.status, "blocked")
        self.assertIn("2026-10-09 held open", plan.reason)
        self.assertEqual(plan.rows, ())

    def test_thin_weekend_stale_or_unanchored_lists_are_refused(self):
        today = date(2026, 10, 9)
        self.assertIn("at least", plan_forward_rows(sessions(date(2026, 10, 1), date(2026, 12, 31)), self.held, today=today).reason)
        with_weekend = sessions(date(2026, 1, 1), date(2027, 6, 30)) + [date(2027, 1, 2)]
        self.assertIn("weekend", plan_forward_rows(with_weekend, self.held, today=today).reason)
        self.assertIn("not after today", plan_forward_rows(sessions(date(2025, 1, 1), date(2026, 9, 30)), self.held, today=today).reason)
        self.assertIn("no overlap", plan_forward_rows(sessions(date(2026, 1, 1), date(2027, 6, 30)), {}, today=today).reason)

    def test_a_list_that_ends_inside_the_held_calendar_adds_nothing(self):
        plan = plan_forward_rows(sessions(date(2026, 1, 1), date(2026, 11, 30)), self.held, today=date(2026, 10, 9))
        self.assertEqual((plan.status, plan.rows), ("ready", ()))


class SyncTests(unittest.TestCase):
    def test_sync_persists_the_plan_and_reports_its_evidence(self):
        held = held_calendar(date(2026, 1, 1), date(2026, 12, 31))
        persisted = []

        async def fetch(capability, params):
            self.assertEqual((capability, params), ("a_share_trading_days", {}))
            return {"request_id": "r1", "data": {"items": [str(day) for day in sessions(date(2026, 1, 1), date(2027, 3, 31))]}}

        async def read_held():
            return held

        async def persist(rows, request_id):
            persisted.append((len(rows), request_id))
            return len(rows) * 3

        result = asyncio.run(sync_forward_calendar(fetch_envelope=fetch, read_held=read_held, persist=persist,
                                                   today=date(2026, 10, 9)))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["calendar_through"], "2027-03-31")
        self.assertEqual(persisted, [(90, "r1")])
        self.assertEqual(result["stored_rows"], 270)

    def _sync_with_list_ending_today(self, held_through: date):
        held = held_calendar(date(2026, 1, 1), held_through)

        async def fetch(_capability, _params):
            # 2026-10-09: Fuyao listed sessions up to the day itself and none after.
            return {"request_id": "r3", "data": {"items": [str(day) for day in sessions(date(2026, 1, 1), date(2026, 10, 9))]}}

        async def read_held():
            return held

        async def persist(*_args):
            raise AssertionError("nothing new to persist")

        return asyncio.run(sync_forward_calendar(fetch_envelope=fetch, read_held=read_held, persist=persist,
                                                 today=date(2026, 10, 9)))

    def test_a_list_ending_today_passes_while_the_held_calendar_runs_a_month_ahead(self):
        result = self._sync_with_list_ending_today(date(2026, 12, 31))
        self.assertEqual((result["status"], result["new_dates"]), ("completed", 0))
        self.assertGreaterEqual(result["runway_sessions"], 20)
        self.assertEqual(result["calendar_through"], "2026-12-31")

    def test_a_list_ending_today_blocks_when_the_held_calendar_is_about_to_run_out(self):
        result = self._sync_with_list_ending_today(date(2026, 10, 30))
        self.assertEqual(result["status"], "blocked")
        self.assertIn("add next year's calendar", result["reason"])
        self.assertLess(result["runway_sessions"], 20)

    def test_an_unreadable_answer_writes_nothing(self):
        async def fetch(_capability, _params):
            return {"request_id": "r2", "data": {"note": "maintenance"}}

        async def never(*_args):
            raise AssertionError("must not read or write")

        result = asyncio.run(sync_forward_calendar(fetch_envelope=fetch, read_held=never, persist=never,
                                                   today=date(2026, 10, 9)))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["data_keys"], ["note"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
