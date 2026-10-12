import unittest
from datetime import date, datetime
from unittest.mock import AsyncMock, patch

from app import main


class DailyCloseCompletionWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconciliation_runs_completion_before_controls_and_returns_it(self):
        order = []
        coverage = {"complete": False, "trade_date": "2026-10-12"}

        async def fake_reconcile(day, *, read, sync):
            await read(day)
            result = await sync(day)
            await read(day)
            return result

        async def completion(day):
            order.append("completion")
            return {"status": "partial"}

        async def controls(day):
            order.append("controls")
            return {"status": "completed"}

        async def database(action, *args, **kwargs):
            if action is main._read_daily_control_coverage:
                return coverage
            return action(*args)

        with patch.object(main, "reconcile_daily_control_coverage", fake_reconcile), \
             patch.object(main, "daily_close_completion", completion), \
             patch.object(main, "sync_full_market_daily_controls", controls), \
             patch.object(main, "run_database_blocking", database):
            result = await main.reconcile_daily_controls(date(2026, 10, 12))
        self.assertEqual(order, ["completion", "controls"])
        self.assertEqual(result["completion"]["status"], "partial")

    async def test_teacher_repair_uses_completion_for_bars_then_controls(self):
        order = []

        async def completion(day):
            order.append("completion")
            return {"status": "completed", "trade_date": str(day)}

        async def controls(day):
            order.append("controls")
            return {"status": "completed"}

        with patch.object(main, "daily_close_completion", completion), \
             patch.object(main, "sync_full_market_daily_controls", controls):
            result = await main.teacher_review_repair_daily(date(2026, 10, 12))
        self.assertEqual(order, ["completion", "controls"])
        self.assertEqual(result["bars"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
