"""The background loops that left main.py (docs/decisions/0008); none had a direct test."""

from __future__ import annotations

import asyncio
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timezone
from unittest.mock import patch

from app import level1_snapshot_runtime as level1
from app import peer_close_research as peer
from app import storage_tiering_mover as mover_module
from app.level1_snapshot_runtime import Level1CaptureDependencies, level1_capture
from app.peer_close_research import PeerCloseDependencies, peer_stages, run_peer_close_loop
from app.storage_tiering_mover import run_mover_loop


class Stop(Exception):
    """Raised by the injected sleep to end a loop after the rounds under test."""


def stop_after(rounds: int, delays: list[float]):
    async def sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) >= rounds:
            raise Stop

    return sleep


async def run_inline(action, *args, **_kwargs):
    return action(*args)


class _Database:
    def __init__(self) -> None:
        self.connection = object()

    @contextmanager
    def transaction(self):
        yield self.connection


class StorageTieringLoopTests(unittest.TestCase):
    def test_reports_on_a_change_and_comes_back_sooner_after_moving_rows(self):
        reports = iter([
            {"status": "partial", "copied_rows": 10, "complete": False},
            {"status": "awaiting_owner_grant"},
            {"status": "awaiting_owner_grant"},
        ])

        class Mover:
            def run_pass(self, *, budget_seconds):
                self.budget = budget_seconds
                return next(reports)

        saved, delays = [], []
        with patch.object(mover_module, "persist_run_report", lambda _db, report: saved.append(report["status"])):
            with self.assertRaises(Stop):
                asyncio.run(run_mover_loop(object(), safe_error=lambda text, _n: text,
                                           sleep=stop_after(3, delays), mover=Mover()))
        self.assertEqual(saved, ["partial", "awaiting_owner_grant"], "an unchanged status is not stored again")
        self.assertEqual(delays, [60.0, 900.0, 900.0])

    def test_a_failed_pass_waits_the_full_interval(self):
        class Broken:
            def run_pass(self, **_kwargs):
                raise RuntimeError("tunnel down")

        delays: list[float] = []
        with self.assertRaises(Stop):
            asyncio.run(run_mover_loop(object(), safe_error=lambda text, _n: text,
                                       sleep=stop_after(1, delays), mover=Broken()))
        self.assertEqual(delays, [900.0])


def peer_deps(*, now: datetime, teacher: bool = True, calls: list | None = None, delays: list | None = None):
    calls = [] if calls is None else calls

    async def calendar_open(_day: date) -> bool:
        return True

    def stage(name):
        async def run(day: date):
            calls.append((name, day))
            return {"status": "completed"}
        return run

    async def record(name, trade_date, action, **_kwargs):
        return await action()

    return PeerCloseDependencies(
        database=_Database(), run_database=run_inline, calendar_open=calendar_open, teacher_enabled=lambda: teacher,
        teacher_roll=stage("roll"), teacher_outcome=stage("outcome"), watch_review=stage("watch"),
        settle_xiaojie=lambda day: calls.append(("xiaojie", day)) or {"status": "completed"},
        daily_digest=stage("digest"), record_stage=record, safe_error=lambda text, _n: text,
        now=lambda: now, sleep=stop_after(1, [] if delays is None else delays),
    )


class PeerCloseLoopTests(unittest.TestCase):
    def test_after_the_close_every_stage_runs_for_today_in_order(self):
        calls, delays = [], []
        deps = peer_deps(now=datetime(2026, 10, 9, 8, 30, tzinfo=timezone.utc), calls=calls, delays=delays)
        with patch.object(peer, "daily_bars_ready", return_value=True):
            with self.assertRaises(Stop):
                asyncio.run(run_peer_close_loop(deps))
        self.assertEqual([name for name, _ in calls], ["roll", "outcome", "watch", "xiaojie", "digest"])
        self.assertEqual({day for _, day in calls}, {date(2026, 10, 9)})
        self.assertEqual(delays, [600])

    def test_nothing_runs_until_the_bars_have_landed(self):
        calls = []
        deps = peer_deps(now=datetime(2026, 10, 9, 8, 30, tzinfo=timezone.utc), calls=calls)
        with patch.object(peer, "daily_bars_ready", return_value=False):
            with self.assertRaises(Stop):
                asyncio.run(run_peer_close_loop(deps))
        self.assertEqual(calls, [])

    def test_teacher_stages_skip_when_teacher_review_is_disabled(self):
        stages = peer_stages(peer_deps(now=datetime(2026, 10, 9, tzinfo=timezone.utc), teacher=False))
        result = asyncio.run(stages["teacher_review_roll"](date(2026, 10, 9)))
        self.assertEqual(result["status"], "skipped")
        self.assertTrue(result["research_only"])


def level1_deps(*, fetch, health: list, session=(True, "open")):
    async def session_open(_now):
        return session

    return Level1CaptureDependencies(
        fetch_snapshot=fetch, persist_observations=lambda _provider, _capability, rows: len(rows),
        run_database=run_inline, database=_Database(), session_open=session_open,
        record_success=lambda _c, provider, capability, rows, _x: health.append(("ok", capability, rows)),
        record_failure=lambda _c, provider, capability, error, _x: health.append(("failed", capability, error)),
        safe_error=lambda text, _n: text,
    )


class Level1CaptureTests(unittest.TestCase):
    def test_a_completed_capture_records_provider_success(self):
        async def fetch():
            return [{"symbol": "000001.SZ"}, {"symbol": "600000.SH"}], {"status": "fresh"}

        health: list = []
        result = asyncio.run(level1_capture(level1_deps(fetch=fetch, health=health))())
        self.assertEqual((result["status"], result["stored"]), ("completed", 2))
        self.assertEqual(health, [("ok", "a_share_prices_snapshot", 2)])

    def test_a_provider_error_is_recorded_and_re_raised(self):
        async def fetch():
            raise RuntimeError("fuyao 502")

        health: list = []
        with self.assertRaises(RuntimeError):
            asyncio.run(level1_capture(level1_deps(fetch=fetch, health=health))())
        self.assertEqual(health, [("failed", "a_share_prices_snapshot", "fuyao 502")])

    def test_outside_the_session_nothing_is_fetched_or_recorded(self):
        async def fetch():
            raise AssertionError("must not fetch outside the session")

        health: list = []
        result = asyncio.run(level1_capture(level1_deps(fetch=fetch, health=health, session=(False, "closed")))())
        self.assertEqual(result["status"], "outside_session")
        self.assertEqual(health, [])

    def test_the_service_runs_on_a_one_minute_cadence(self):
        seen = {}

        async def fake_loop(**kwargs):
            seen.update(kwargs)

        with patch.object(level1, "run_level1_snapshot_loop", fake_loop):
            asyncio.run(level1.run_level1_capture_service(level1_deps(fetch=None, health=[])))
        self.assertEqual(seen["interval_seconds"], 60)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
