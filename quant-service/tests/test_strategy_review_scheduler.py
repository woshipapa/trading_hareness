from __future__ import annotations

import asyncio
from datetime import date, datetime
import unittest
from zoneinfo import ZoneInfo

from app.strategy_review_scheduler import StrategyReviewSchedulerDependencies, strategy_review_scheduler_step


CN = ZoneInfo("Asia/Shanghai")


class StrategyReviewSchedulerTests(unittest.IsolatedAsyncioTestCase):
    def _dependencies(self, *, calendar_is_open=True, fail_operation: str | None = None):
        calls: list[tuple[str, object]] = []

        async def calendar_open(day):
            calls.append(("calendar", day))
            return calendar_is_open

        async def operation(name, *args):
            calls.append((name, args))
            if name == fail_operation:
                raise RuntimeError(name)
            return {"status": "completed"}

        dependencies = StrategyReviewSchedulerDependencies(
            calendar_open=calendar_open,
            sync_index_context=lambda day: operation("index", day),
            build_market_snapshot=lambda day, session: operation("snapshot", day, session),
            build_board_report=lambda: operation("board"),
            recompute_outcomes=lambda day: operation("outcomes", day),
            recompute_analyst_intraday_outcomes=lambda day: operation("analyst_intraday_outcomes", day),
            recompute_scorecards=lambda day: operation("scorecards", day),
            persist_review=lambda day, session: operation("persist", day, session),
            now=lambda: datetime(2026, 8, 14, 11, 31, tzinfo=CN),
            report_error=lambda _message: None,
        )
        return dependencies, calls

    async def test_step_does_nothing_when_exchange_is_closed(self):
        dependencies, calls = self._dependencies(calendar_is_open=False)

        result = await strategy_review_scheduler_step(set(), dependencies)

        self.assertEqual(result, ())
        self.assertEqual([name for name, _ in calls], ["calendar"])

    async def test_midday_only_builds_snapshot_board_and_review(self):
        dependencies, calls = self._dependencies()
        completed: set[tuple[object, str]] = set()

        result = await strategy_review_scheduler_step(
            completed, dependencies, local=datetime(2026, 8, 14, 11, 31, 30, tzinfo=CN),
        )

        self.assertEqual(result, ("midday",))
        self.assertEqual([name for name, _ in calls], ["calendar", "snapshot", "board", "persist"])
        self.assertIn((datetime(2026, 8, 14, tzinfo=CN).date(), "midday"), completed)

    async def test_close_runs_index_settlement_and_scorecards_before_review(self):
        dependencies, calls = self._dependencies()
        completed: set[tuple[object, str]] = set()

        result = await strategy_review_scheduler_step(
            completed, dependencies, local=datetime(2026, 8, 14, 15, 5, 30, tzinfo=CN),
        )

        self.assertEqual(result, ("close",))
        self.assertEqual(
            [name for name, _ in calls],
            ["calendar", "index", "snapshot", "board", "outcomes", "analyst_intraday_outcomes", "scorecards", "persist"],
        )
        self.assertIn((datetime(2026, 8, 14, tzinfo=CN).date(), "close"), completed)

    async def test_close_index_and_snapshot_start_without_waiting_for_each_other(self):
        started: list[str] = []
        release = asyncio.Event()

        async def operation(name, *args):
            started.append(name)
            if name in {"index", "snapshot"}:
                await release.wait()
            return {"status": "completed"}

        dependencies, _calls = self._dependencies()
        dependencies = dependencies.__class__(**{
            **dependencies.__dict__,
            "sync_index_context": lambda day: operation("index", day),
            "build_market_snapshot": lambda day, session: operation("snapshot", day, session),
        })
        task = asyncio.create_task(strategy_review_scheduler_step(
            set(), dependencies, local=datetime(2026, 8, 14, 15, 5, 30, tzinfo=CN),
        ))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(set(started), {"index", "snapshot"})
        release.set()
        self.assertEqual(await task, ("close",))

    async def test_failed_checkpoint_is_not_completed_and_can_retry(self):
        dependencies, calls = self._dependencies(fail_operation="board")
        completed: set[tuple[object, str]] = set()

        result = await strategy_review_scheduler_step(
            completed, dependencies, local=datetime(2026, 8, 14, 11, 31, tzinfo=CN),
        )

        self.assertEqual(result, ())
        self.assertEqual(completed, set())
        self.assertEqual([name for name, _ in calls], ["calendar", "snapshot", "board"])

    async def test_restart_reuses_completed_checkpoint_without_repeating_side_effects(self):
        dependencies, calls = self._dependencies()
        receipt_calls: list[tuple[object, str]] = []

        async def completed_for_checkpoint(day, session):
            receipt_calls.append((day, session))
            return session == "midday"

        dependencies = dependencies.__class__(**{
            **dependencies.__dict__, "completed_for_checkpoint": completed_for_checkpoint,
        })
        completed: set[tuple[object, str]] = set()
        result = await strategy_review_scheduler_step(
            completed, dependencies, local=datetime(2026, 8, 14, 11, 31, 30, tzinfo=CN),
        )
        self.assertEqual(result, ("midday",))
        self.assertEqual(receipt_calls, [(datetime(2026, 8, 14, tzinfo=CN).date(), "midday")])
        self.assertEqual([name for name, _ in calls], ["calendar"])
        self.assertIn((datetime(2026, 8, 14, tzinfo=CN).date(), "midday"), completed)

    async def test_close_generates_daily_and_friday_weekly_analyst_reviews(self):
        dependencies, calls = self._dependencies()
        analyst_reviews: list[tuple[str, object]] = []

        async def build_review(cadence, day):
            analyst_reviews.append((cadence, day))

        dependencies = dependencies.__class__(**{
            **dependencies.__dict__, "build_analyst_market_review": build_review,
        })
        completed: set[tuple[object, str]] = set()
        await strategy_review_scheduler_step(
            completed, dependencies, local=datetime(2026, 8, 14, 15, 5, 30, tzinfo=CN),
        )
        self.assertEqual([cadence for cadence, _ in analyst_reviews], ["daily", "weekly"])

    def _with_receipt(self, dependencies, *, stored: bool):
        receipt_calls: list[tuple[object, str]] = []

        async def completed_for_checkpoint(day, session):
            receipt_calls.append((day, session))
            return stored

        return dependencies.__class__(**{
            **dependencies.__dict__, "completed_for_checkpoint": completed_for_checkpoint,
        }), receipt_calls

    async def test_close_is_caught_up_after_its_window_when_no_receipt_exists(self):
        dependencies, calls = self._dependencies()
        dependencies, _receipts = self._with_receipt(dependencies, stored=False)
        completed: set[tuple[date, str]] = set()

        result = await strategy_review_scheduler_step(
            completed, dependencies, local=datetime(2026, 8, 14, 17, 20, tzinfo=CN),
        )

        self.assertEqual(result, ("close",))
        self.assertIn("scorecards", [name for name, _ in calls])
        self.assertIn((date(2026, 8, 14), "close"), completed)

    async def test_catch_up_stores_nothing_twice_and_leaves_midday_alone(self):
        dependencies, calls = self._dependencies()
        stored, _receipts = self._with_receipt(dependencies, stored=True)
        completed: set[tuple[date, str]] = set()
        result = await strategy_review_scheduler_step(
            completed, stored, local=datetime(2026, 8, 14, 17, 20, tzinfo=CN),
        )
        self.assertEqual(result, ("close",), "a receipt exists: the checkpoint is marked done, nothing is rebuilt")
        self.assertEqual([name for name, _ in calls], ["calendar"])

        missing, _receipts = self._with_receipt(dependencies, stored=False)
        calls.clear()
        result = await strategy_review_scheduler_step(
            set(), missing, local=datetime(2026, 8, 14, 12, 30, tzinfo=CN),
        )
        self.assertEqual(result, (), "the midday snapshot is a point in time and is not caught up")
        self.assertEqual([name for name, _ in calls], ["calendar"])

    async def test_catch_up_needs_the_durable_receipt_and_the_evening_window(self):
        dependencies, calls = self._dependencies()
        for local, deps in ((datetime(2026, 8, 14, 17, 20, tzinfo=CN), dependencies),
                            (datetime(2026, 8, 14, 22, 5, tzinfo=CN), self._with_receipt(dependencies, stored=False)[0]),
                            (datetime(2026, 8, 14, 15, 7, tzinfo=CN), self._with_receipt(dependencies, stored=False)[0])):
            calls.clear()
            with self.subTest(local=local.time()):
                result = await strategy_review_scheduler_step(set(), deps, local=local)
                self.assertEqual(result, ())
                self.assertEqual([name for name, _ in calls], ["calendar"])

    async def test_a_failing_catch_up_is_tried_every_ten_minutes_not_every_turn(self):
        dependencies, calls = self._dependencies(fail_operation="board")
        dependencies, _receipts = self._with_receipt(dependencies, stored=False)
        completed: set[tuple[date, str]] = set()
        attempts: dict[tuple[date, str], datetime] = {}
        for minute in (20, 21, 29, 31):
            await strategy_review_scheduler_step(
                completed, dependencies, local=datetime(2026, 8, 14, 17, minute, tzinfo=CN), attempts=attempts,
            )
        self.assertEqual([name for name, _ in calls].count("board"), 2, "17:20 and 17:31, not the turns between")


if __name__ == "__main__":
    unittest.main()
