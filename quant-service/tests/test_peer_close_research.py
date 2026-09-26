"""The peer's own close stages run once per session, after the bars land."""

from __future__ import annotations

import asyncio
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.peer_close_research import STAGES, run_due, target_session

CN = ZoneInfo("Asia/Shanghai")
OPEN_DAYS = {date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 24)}


async def calendar_open(day):
    return day in OPEN_DAYS


def at(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=CN)


class TargetSessionTests(unittest.TestCase):
    def test_after_the_close_it_is_today_and_overnight_it_catches_up_yesterday(self):
        self.assertEqual(asyncio.run(target_session(at(date(2026, 9, 22), 16, 30), calendar_open)), date(2026, 9, 22))
        self.assertEqual(asyncio.run(target_session(at(date(2026, 9, 23), 1, 0), calendar_open)), date(2026, 9, 22))

    def test_nothing_runs_during_the_session_or_on_a_holiday(self):
        self.assertIsNone(asyncio.run(target_session(at(date(2026, 9, 23), 10, 0), calendar_open)))
        self.assertIsNone(asyncio.run(target_session(at(date(2026, 9, 25), 17, 0), calendar_open)))   # 中秋
        # The holiday morning still catches up the last session.
        self.assertEqual(asyncio.run(target_session(at(date(2026, 9, 26), 8, 0), calendar_open)), date(2026, 9, 24))


class RunDueTests(unittest.TestCase):
    def test_every_stage_runs_behind_its_receipt_and_a_failure_does_not_stop_the_rest(self):
        ran, recorded = [], []

        async def ok(trade_date):
            ran.append(trade_date)
            return {"status": "completed"}

        async def broken(trade_date):
            raise RuntimeError("provider down")

        async def record(name, trade_date, action):
            recorded.append(name)
            return await action()

        stages = {"teacher_review_roll": broken, "teacher_outcome_review": ok,
                  "watch_daily_review": ok, "xiaojie_outcomes": ok, "daily_digest": ok}
        result = asyncio.run(run_due(date(2026, 9, 22), stages=stages, record=record))
        self.assertEqual(recorded, list(STAGES))
        self.assertTrue(result["stages"]["teacher_review_roll"].startswith("failed: provider down"))
        self.assertEqual(result["stages"]["xiaojie_outcomes"], "completed")
        self.assertEqual(ran, [date(2026, 9, 22)] * 4)


if __name__ == "__main__":
    unittest.main()
