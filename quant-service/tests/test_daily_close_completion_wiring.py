import asyncio
import unittest
from contextlib import contextmanager
from datetime import date
from unittest.mock import patch

from app import main
from app.daily_control_reconciliation import reconcile
from app.post_close_refresh import run_refresh


DAY = date(2026, 10, 12)


class _Db:
    def __init__(self):
        self.statements = []

    @contextmanager
    def transaction(self):
        yield self

    def execute(self, sql, params=None):
        self.statements.append((sql, params))


class DailyCloseCompletionWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_reconciliation_runs_completion_before_controls_and_returns_both(self):
        before = {"complete": False, "trade_date": str(DAY)}
        after = {"complete": True, "trade_date": str(DAY)}
        reads = [before, after]
        order = []

        async def read(_day):
            return reads.pop(0)

        async def sync(_day):
            order.append("completion")
            completion = {"status": "partial"}
            order.append("controls")
            return {"completion": completion, "controls": {"status": "completed"}}

        result = await reconcile(DAY, read=read, sync=sync)
        self.assertEqual(order, ["completion", "controls"])
        self.assertEqual(result["provider_sync"]["completion"]["status"], "partial")
        self.assertEqual(result["provider_sync"]["controls"]["status"], "completed")

    async def test_real_reconciliation_does_not_call_completion_when_complete(self):
        calls = []

        async def read(_day):
            return {"complete": True, "trade_date": str(DAY)}

        async def sync(_day):
            calls.append(True)
            return {}

        result = await reconcile(DAY, read=read, sync=sync)
        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(calls, [])

    async def test_main_completion_defers_factory_and_records_blocked_receipt(self):
        db = _Db()
        state = {"roster": [], "bars": []}

        async def database(action, *args, **_kwargs):
            return action(*args)

        def raising_factory():
            raise RuntimeError("transport unavailable")

        with patch("app.daily_close_completion._read_state", return_value=state), \
             patch.object(main, "db", db), \
             patch.object(main, "run_database_blocking", database), \
             patch.object(main, "longhu_full_market_source_factory", raising_factory):
            result = await main.daily_close_completion(DAY)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("fetch_runs" in sql for sql, _params in db.statements))

    async def test_partial_blocked_and_failed_stage_results_do_not_stop_next_stage(self):
        for status in ("partial", "blocked", "failed"):
            calls = []
            async def run_db(action, *args, **_kwargs):
                return action(*args)

            result = await run_refresh(
                object(), db=object(), lease_key="lease", lease_seconds=lambda: 60,
                run_database_blocking=run_db, acquire_lease=lambda *_: True,
                renew_lease=lambda *_: True, release_lease=lambda *_: None,
                actions={"daily_close_completion": lambda: {"status": status},
                         "next": lambda: calls.append("next") or {"status": "completed"}},
                stage_order=("daily_close_completion", "next"), trade_date=DAY,
                safe_error_detail=lambda value, _limit: value, json_safe=lambda value: value,
            )
            self.assertEqual(calls, ["next"])
            self.assertEqual(result["stages"]["daily_close_completion"]["status"], status)


if __name__ == "__main__":
    unittest.main()
