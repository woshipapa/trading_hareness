"""Multi-source history backfill: parsing, pagination, resumability and the session window."""

from __future__ import annotations

import asyncio
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.market_history_backfill import (_info_rows, backfill_allowed, fuyao_limit_pools, longhu_limit_performance,
                                         longhu_morning_bidding, walk_source)

CN = ZoneInfo("Asia/Shanghai")


class Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeDatabase:
    def __init__(self, stored_days=()):
        self.stored_days, self.inserts = set(stored_days), []

    def transaction(self):
        database = self

        class Tx:
            def __enter__(self):
                return database

            def __exit__(self, *exc):
                return False
        return Tx()

    def execute(self, sql, params=()):
        if sql.lstrip().startswith("SELECT 1 FROM quant.raw_market_observations"):
            return Result({"x": 1} if params[3].date() in self.stored_days else None)
        if sql.lstrip().startswith("INSERT INTO quant.raw_market_observations"):
            self.inserts.append(params)
            return Result({"observation_id": len(self.inserts)})
        raise AssertionError(sql)


async def run_database(fn, *args, timeout_seconds=None):
    return fn(*args)


class MarketHistoryBackfillTests(unittest.TestCase):
    def test_longhu_list_shapes_and_limit_performance_calls(self):
        self.assertEqual(_info_rows({"info": [[["000917", "电广传媒"]], {"extra": 1}]}), [["000917", "电广传媒"]])
        self.assertEqual(_info_rows({"info": [[64, 5, "2026-09-22"]]}), [[64, 5, "2026-09-22"]])
        calls = []

        async def call(request):
            calls.append(request["params"])
            return {"pages": [{"payload": {"info": [[["000917", "电广传媒"]], {}], "errcode": "0"}}]}

        lists = asyncio.run(longhu_limit_performance(call)(date(2024, 3, 15)))
        self.assertEqual([params["PidType"] for params in calls], ["1", "2", "3", "4", "5"])
        self.assertTrue(all(params["Day"] == "2024-03-15" for params in calls))
        self.assertEqual(lists["pid:1"][0], [["000917", "电广传媒"]])

    def test_pagination_stops_on_a_short_page(self):
        pages = []

        async def fetch_fuyao(capability, params):
            pages.append((capability, params["page"]))
            return {"item": [{"thscode": "X"}] * (50 if params["page"] == 1 else 3)}

        lists = asyncio.run(fuyao_limit_pools(fetch_fuyao)(date(2024, 3, 15)))
        self.assertEqual(len(lists["limit_up"][0]), 53)
        self.assertEqual([page for capability, page in pages if capability == "a_share_limit_up_pool"], [1, 2])

        async def call(request):
            index = int(request["params"]["Index"])
            return {"pages": [{"payload": {"info": [[[str(i)] for i in range(60 if index == 0 else 10)]]}}]}

        auction = asyncio.run(longhu_morning_bidding(call)(date(2025, 9, 1)))
        self.assertEqual(len(auction["auction"][0]), 70)

    def test_walk_skips_stored_days_survives_errors_and_stops_at_the_session(self):
        database = FakeDatabase(stored_days={date(2025, 1, 3)})
        allowed_answers = iter([True, True, True, False])

        async def allowed():
            return next(allowed_answers)

        async def fetch(day):
            if day == date(2025, 1, 2):
                raise RuntimeError("gateway 503")
            return {"auction": ([["600000"]], {})}

        days = [date(2025, 1, 3), date(2025, 1, 2), date(2024, 12, 31), date(2024, 12, 30)]
        report = asyncio.run(walk_source("longhu_morning_bidding", days, fetch, database=database, run_database=run_database,
                                         allowed=allowed, pace_seconds=0))
        self.assertEqual(report["skipped"], 1)
        self.assertIn("2025-01-02", report["errors"])
        self.assertEqual(report["stored"], 1)                       # only 2024-12-31
        self.assertIn("2024-12-30", report["stopped"])
        self.assertEqual(database.inserts[0][2], "auction")

    def test_never_during_the_trading_session(self):
        self.assertFalse(backfill_allowed(datetime(2026, 9, 23, 10, 0, tzinfo=CN), True))
        self.assertTrue(backfill_allowed(datetime(2026, 9, 23, 16, 0, tzinfo=CN), True))
        self.assertTrue(backfill_allowed(datetime(2026, 9, 26, 10, 0, tzinfo=CN), False))


if __name__ == "__main__":
    unittest.main()
