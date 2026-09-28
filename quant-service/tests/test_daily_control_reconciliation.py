from __future__ import annotations

import unittest
from datetime import date

from app.daily_control_reconciliation import (
    DEFAULT_SOURCE_PLAN,
    needs_reconciliation,
    normalize_coverage,
    reconcile,
)


class DailyControlReconciliationTests(unittest.IsolatedAsyncioTestCase):
    def test_complete_coverage_does_not_fetch_again(self):
        coverage = normalize_coverage({
            "expected_symbols": 5_500,
            "bar_symbols": 5_402,
            "fundamental_symbols": 5_402,
            "adjustment_symbols": 5_402,
            "limit_symbols": 5_402,
        }, date(2026, 9, 28))
        self.assertFalse(needs_reconciliation(coverage))
        self.assertEqual(coverage["missing"], {
            "daily_bars": 0, "daily_basic": 0, "adjustment_factor": 0, "trade_limits": 0,
        })

    def test_fundamental_gap_is_a_blocking_repair_target(self):
        coverage = normalize_coverage({
            "expected_symbols": 5_500,
            "bar_symbols": 5_402,
            "fundamental_symbols": 5_095,
            "adjustment_symbols": 5_402,
            "limit_symbols": 5_402,
        }, date(2026, 9, 28))
        self.assertTrue(needs_reconciliation(coverage))
        self.assertEqual(coverage["missing"]["daily_basic"], 307)
        self.assertEqual(coverage["missing"]["adjustment_factor"], 0)

    async def test_reconcile_fetches_then_reads_back_before_completion(self):
        before = normalize_coverage({
            "expected_symbols": 5_500, "bar_symbols": 5_402,
            "fundamental_symbols": 5_095, "adjustment_symbols": 5_402, "limit_symbols": 5_402,
        }, date(2026, 9, 28))
        after = normalize_coverage({
            "expected_symbols": 5_500, "bar_symbols": 5_402,
            "fundamental_symbols": 5_402, "adjustment_symbols": 5_402, "limit_symbols": 5_402,
        }, date(2026, 9, 28))
        reads = [before, after]
        calls: list[date] = []

        async def read(day: date):
            return reads.pop(0)

        async def sync(day: date):
            calls.append(day)
            return {"status": "completed", "providers": {"daily_basic": "tushare_super_get"}}

        result = await reconcile(date(2026, 9, 28), read=read, sync=sync)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(calls, [date(2026, 9, 28)])
        self.assertEqual(result["provider_sync"]["providers"]["daily_basic"], "tushare_super_get")
        self.assertEqual(result["source_plan"], DEFAULT_SOURCE_PLAN)

    async def test_reconcile_stays_blocked_when_provider_remains_partial(self):
        coverage = normalize_coverage({
            "expected_symbols": 5_500, "bar_symbols": 5_402,
            "fundamental_symbols": 5_095, "adjustment_symbols": 5_402, "limit_symbols": 5_402,
        }, date(2026, 9, 28))

        async def read(_day: date):
            return coverage

        async def sync(_day: date):
            return {"status": "blocked", "reason": "provider incomplete"}

        result = await reconcile(date(2026, 9, 28), read=read, sync=sync)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("coverage", result["reason"])


if __name__ == "__main__":
    unittest.main()
