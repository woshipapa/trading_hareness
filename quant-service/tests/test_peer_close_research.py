"""The peer's own close stages run once per session, after the bars land."""

from __future__ import annotations

import asyncio
import unittest
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from app import peer_close_research as peer
from app.peer_close_research import (
    STAGES, PeerCloseDependencies, open_stages, peer_stages, previous_session, run_due, target_session,
)

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
        self.assertEqual(recorded, list(STAGES[:5]), "the teacher stages keep the first five places")
        self.assertTrue(result["stages"]["teacher_review_roll"].startswith("failed: provider down"))
        self.assertEqual(result["stages"]["xiaojie_outcomes"], "completed")
        self.assertEqual(ran, [date(2026, 9, 22)] * 4)

    def test_the_risk_reading_waits_for_the_lists_and_the_tick_says_what_it_left_open(self):
        ran = []

        def stage(name, status, reason=None):
            async def run(trade_date):
                ran.append(name)
                return {"status": status, **({"reason": reason} if reason else {})}
            return run

        async def record(name, trade_date, action):
            return await action()

        stages = {"peer_limit_performance_lists": stage("lists", "partial", "the live lists came back empty"),
                  "peer_limit_up_risk": stage("risk", "completed")}
        result = asyncio.run(run_due(date(2026, 9, 22), stages=stages, record=record))
        self.assertEqual(ran, ["lists"], "the risk refresh must not run on lists that are not there")
        self.assertEqual(result["stages"], {"peer_limit_performance_lists": "partial", "peer_limit_up_risk": "blocked"})
        self.assertEqual(open_stages(result), "peer close research 2026-09-22 left open: "
                         "peer_limit_performance_lists=partial (the live lists came back empty); "
                         "peer_limit_up_risk=blocked (waiting for peer_limit_performance_lists)")

        ran.clear()
        stages["peer_limit_performance_lists"] = stage("lists", "completed")
        result = asyncio.run(run_due(date(2026, 9, 22), stages=stages, record=record))
        self.assertEqual(ran, ["lists", "risk"])
        self.assertEqual(open_stages(result), "")


def product_deps(*, now):
    async def run_inline(function, *args, **_kwargs):
        return function(*args)

    async def unused(_day):
        raise AssertionError("not part of these tests")

    async def call(_request):
        raise AssertionError("the tests patch the fetchers")

    return PeerCloseDependencies(
        database=object(), run_database=run_inline, calendar_open=calendar_open, teacher_enabled=lambda: True,
        teacher_roll=unused, teacher_outcome=unused, watch_review=unused, settle_xiaojie=lambda _day: None,
        daily_digest=unused, record_stage=None, safe_error=lambda text, _n: text, now=lambda: now, call_longhu=call)


class ProductStageTests(unittest.TestCase):
    """The receipts of the daily products close only when the product is stored."""

    TODAY = date(2026, 9, 22)

    def lists(self, *, live_stored=False, live=None, history=None, day=None):
        stages = peer_stages(product_deps(now=at(self.TODAY, 16, 30)))
        calls = []

        async def append(trade_date, _call, **_kwargs):
            calls.append("live")
            return live

        async def walk(name, days, _fetch, **_kwargs):
            calls.append("history")
            return {"source": name, "days": 1, "stored": 0, "skipped": 0, "empty": 0, "errors": {}, "stopped": None,
                    **history}

        with mock.patch.object(peer, "_live_lists_stored", return_value=live_stored), \
             mock.patch.object(peer, "append_limit_performance", append), mock.patch.object(peer, "walk_source", walk):
            result = asyncio.run(stages["peer_limit_performance_lists"](day or self.TODAY))
        return result["status"], calls

    def test_tonight_the_live_lists_are_taken_and_an_empty_answer_leaves_the_receipt_open(self):
        self.assertEqual(self.lists(live={"status": "completed", "stored": 5}), ("completed", ["live"]))
        self.assertEqual(self.lists(live={"status": "unchanged", "stored": 0}), ("completed", ["live"]))
        self.assertEqual(self.lists(live={"status": "empty", "stored": 0}), ("partial", ["live"]))
        self.assertEqual(self.lists(live={"status": "skipped", "reason": "not an open session"}), ("partial", ["live"]))

    def test_a_day_whose_live_lists_are_stored_asks_no_source(self):
        self.assertEqual(self.lists(live_stored=True), ("completed", []))
        self.assertEqual(self.lists(live_stored=True, day=date(2026, 9, 21)), ("completed", []))

    def test_another_day_without_live_lists_goes_to_the_history_source(self):
        yesterday = date(2026, 9, 21)
        self.assertEqual(self.lists(history={"stored": 5}, day=yesterday), ("completed", ["history"]))
        self.assertEqual(self.lists(history={"skipped": 1}, day=yesterday), ("completed", ["history"]))
        self.assertEqual(self.lists(history={"empty": 1}, day=yesterday), ("partial", ["history"]))
        self.assertEqual(self.lists(history={"errors": {"2026-09-21": "errcode"}}, day=yesterday), ("partial", ["history"]))
        self.assertEqual(self.lists(history={"stopped": "session window reached"}, day=yesterday), ("blocked", ["history"]))

    def test_a_reading_without_lists_is_blocked_not_completed(self):
        stages = peer_stages(product_deps(now=at(self.TODAY, 16, 30)))
        with mock.patch.object(peer, "refresh_limit_up_risk", return_value={"status": "no_lists", "stored": 0}):
            self.assertEqual(asyncio.run(stages["peer_limit_up_risk"](self.TODAY))["status"], "blocked")
        with mock.patch.object(peer, "refresh_limit_up_risk", return_value={"status": "completed", "rows": 67}):
            self.assertEqual(asyncio.run(stages["peer_limit_up_risk"](self.TODAY)), {"status": "completed", "rows": 67})

    def test_without_the_licensed_call_the_products_are_not_wired(self):
        deps = product_deps(now=at(self.TODAY, 16, 30))
        bare = PeerCloseDependencies(**{**deps.__dict__, "call_longhu": None})
        self.assertEqual(set(peer_stages(bare)), set(STAGES[:5]))
        self.assertEqual(set(peer_stages(deps)), set(STAGES[:8]))

    def test_the_session_before_skips_the_holiday(self):
        self.assertEqual(asyncio.run(previous_session(date(2026, 9, 28), calendar_open_with_gap)), date(2026, 9, 24))


async def calendar_open_with_gap(day):
    return day in OPEN_DAYS or day == date(2026, 9, 28)


if __name__ == "__main__":
    unittest.main()
