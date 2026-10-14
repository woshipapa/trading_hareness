"""Multi-source history backfill: parsing, pagination, resumability and the session window."""

from __future__ import annotations

import asyncio
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.market_history_backfill import (append_limit_performance, _info_rows, backfill_allowed,
                                         fuyao_limit_pools, longhu_limit_performance,
                                         longhu_limit_performance_live, longhu_morning_bidding, walk_source)

CN = ZoneInfo("Asia/Shanghai")
AFTER_CLOSE = datetime(2026, 9, 18, 15, 1, tzinfo=CN)


class Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeDatabase:
    def __init__(self, stored_days=(), *, calendar_open=True):
        self.stored_days, self.stored_items, self.hashes, self.inserts = set(stored_days), set(), set(), []
        self.calendar_open = calendar_open

    def transaction(self):
        database = self

        class Tx:
            def __enter__(self):
                return database

            def __exit__(self, *exc):
                return False
        return Tx()

    def execute(self, sql, params=()):
        if "quant.market_trade_calendar" in sql:
            return Result({"is_open": self.calendar_open})
        if sql.lstrip().startswith("SELECT 1 FROM quant.raw_market_observations"):
            key = (params[0], params[1], params[2], params[3].date())
            return Result({"x": 1} if params[3].date() in self.stored_days or key in self.stored_items else None)
        if sql.lstrip().startswith("INSERT INTO quant.raw_market_observations"):
            if params[5] in self.hashes:
                return Result(None)
            self.hashes.add(params[5])
            self.inserts.append(params)
            self.stored_items.add((params[0], params[1], params[2], params[3].date()))
            return Result({"observation_id": len(self.inserts)})
        raise AssertionError(sql)


async def run_database(fn, *args, timeout_seconds=None):
    return fn(*args)


class MarketHistoryBackfillTests(unittest.TestCase):
    def test_longhu_list_shapes_and_limit_performance_calls(self):
        self.assertEqual(_info_rows({"info": [[["000917", "电广传媒"]], {"extra": 1}]}), [["000917", "电广传媒"]])
        self.assertEqual(_info_rows({"info": [[64, 5, "2026-09-22"]]}), [[64, 5, "2026-09-22"]])
        # What the vendor returned for the empty four-board list of 2026-10-13 (BACKLOG B045).
        self.assertEqual(_info_rows({"info": [[], "暂无数据"]}), [])
        self.assertEqual(_info_rows({"info": [[]]}), [])
        self.assertEqual(_info_rows({"info": []}), [])
        calls = []

        async def call(request):
            calls.append(request["params"])
            return {"pages": [{"payload": {"info": [[["000917", "电广传媒"]], {}], "errcode": "0"}}]}

        lists = asyncio.run(longhu_limit_performance(call)(date(2024, 3, 15)))
        self.assertEqual([params["PidType"] for params in calls], ["1", "2", "3", "4", "5"])
        self.assertTrue(all(params["Day"] == "2024-03-15" for params in calls))
        self.assertEqual(lists["pid:1"][0], [["000917", "电广传媒"]])

    def test_live_limit_performance_uses_quote_target_without_day(self):
        calls = []

        async def call(request):
            calls.append(request)
            return {"pages": [{"payload": {"info": [[ ["000917", "电广传媒"] ]], "errcode": "0"}}]}

        lists = asyncio.run(longhu_limit_performance_live(call)(date(2026, 9, 18)))
        self.assertEqual(len(calls), 5)
        self.assertTrue(all(request["target"] == "longhu_quote" for request in calls))
        self.assertTrue(all(request["params"]["c"] == "HomeDingPan" for request in calls))
        self.assertTrue(all("Day" not in request["params"] for request in calls))
        self.assertEqual(lists["pid:1"][1]["target"], "longhu_quote")

    def test_live_limit_performance_empty_day_is_not_stored_and_retries(self):
        database = FakeDatabase()
        calls = 0

        async def fetch(_request):
            nonlocal calls
            calls += 1
            return {"pages": [{"payload": {"info": [], "errcode": "0"}}]}

        first = asyncio.run(append_limit_performance(
            date(2026, 9, 18), fetch, database=database, run_database=run_database, now=AFTER_CLOSE,
        ))
        second = asyncio.run(append_limit_performance(
            date(2026, 9, 18), fetch, database=database, run_database=run_database, now=AFTER_CLOSE,
        ))
        self.assertEqual(first["status"], "empty")
        self.assertEqual(second["status"], "empty")
        self.assertEqual(calls, 10)
        self.assertEqual(database.inserts, [])

    def test_live_limit_performance_non_empty_day_is_idempotent(self):
        database = FakeDatabase()

        async def fetch(_request):
            return {"pages": [{"payload": {"info": [[ ["000917", "电广传媒"] ]], "errcode": "0"}}]}

        first = asyncio.run(append_limit_performance(
            date(2026, 9, 18), fetch, database=database, run_database=run_database, now=AFTER_CLOSE,
        ))
        second = asyncio.run(append_limit_performance(
            date(2026, 9, 18), fetch, database=database, run_database=run_database, now=AFTER_CLOSE,
        ))
        self.assertEqual(first["stored"], 5)
        self.assertEqual(second["stored"], 0)
        self.assertEqual(len(database.inserts), 5)
        self.assertTrue(all(params[1] == "limit_performance_live" for params in database.inserts))
        self.assertTrue(all(params[7].obj["meta"]["target"] == "longhu_quote" for params in database.inserts))

    def test_live_limit_performance_changed_revision_is_stored(self):
        database = FakeDatabase()
        state = {"value": "first"}

        async def fetch(_request):
            return {"pages": [{"payload": {"info": [[[state["value"]] * 23]], "errcode": "0"}}]}

        asyncio.run(append_limit_performance(
            date(2026, 9, 18), fetch, database=database, run_database=run_database, now=AFTER_CLOSE,
        ))
        state["value"] = "second"
        result = asyncio.run(append_limit_performance(
            date(2026, 9, 18), fetch, database=database, run_database=run_database, now=AFTER_CLOSE,
        ))
        self.assertEqual(result["stored"], 5)
        self.assertEqual(len(database.inserts), 10)

    def test_live_limit_performance_empty_revision_does_not_replace_list(self):
        database = FakeDatabase()
        state = {"empty_pid": None}

        async def fetch(request):
            pid = request["params"]["PidType"]
            if pid == state["empty_pid"]:
                rows = []
            else:
                rows = [[pid]]
            return {"pages": [{"payload": {"info": [rows] if rows else [], "errcode": "0"}}]}

        asyncio.run(append_limit_performance(
            date(2026, 9, 18), fetch, database=database, run_database=run_database, now=AFTER_CLOSE,
        ))
        state["empty_pid"] = "1"
        result = asyncio.run(append_limit_performance(
            date(2026, 9, 18), fetch, database=database, run_database=run_database, now=AFTER_CLOSE,
        ))
        self.assertEqual(result["stored"], 0)
        pid_one = [params for params in database.inserts if params[2] == "pid:1"]
        self.assertEqual(len(pid_one), 1)
        self.assertEqual(pid_one[0][7].obj["rows"], [["1"]])

    def test_live_limit_performance_vendor_error_stores_nothing(self):
        database = FakeDatabase()

        async def fetch(_request):
            return {"pages": [{"payload": {"info": [], "errcode": "1020"}}]}

        with self.assertRaisesRegex(RuntimeError, "errcode=1020"):
            asyncio.run(append_limit_performance(
                date(2026, 9, 18), fetch, database=database, run_database=run_database, now=AFTER_CLOSE,
            ))
        self.assertEqual(database.inserts, [])

    def test_live_limit_performance_skips_non_session_dates(self):
        database = FakeDatabase(calendar_open=False)
        calls = 0

        async def fetch(_request):
            nonlocal calls
            calls += 1
            return {"pages": [{"payload": {"info": [[ ["000917"] ]]}}]}

        result = asyncio.run(append_limit_performance(
            date(2026, 9, 19), fetch, database=database, run_database=run_database,
            now=datetime(2026, 9, 19, 15, 1, tzinfo=CN),
        ))
        self.assertEqual(result["status"], "skipped")
        self.assertEqual((calls, database.inserts), (0, []))

    def test_live_limit_performance_skips_past_days_and_before_close(self):
        database = FakeDatabase()
        calls = 0

        async def fetch(_request):
            nonlocal calls
            calls += 1
            return {"pages": [{"payload": {"info": [[ ["000917"] ]]}}]}

        past = asyncio.run(append_limit_performance(
            date(2026, 9, 17), fetch, database=database, run_database=run_database, now=AFTER_CLOSE,
        ))
        before = asyncio.run(append_limit_performance(
            date(2026, 9, 18), fetch, database=database, run_database=run_database,
            now=datetime(2026, 9, 18, 14, 59, tzinfo=CN),
        ))
        self.assertEqual((past["status"], before["status"], calls, database.inserts), ("skipped", "skipped", 0, []))

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

    def test_history_limit_performance_empty_day_is_reported_and_fetched_again(self):
        database = FakeDatabase()
        fetches = 0

        async def allowed():
            return True

        async def fetch(_day):
            nonlocal fetches
            fetches += 1
            return {f"pid:{pid}": ([], {}) for pid in "12345"}

        report = asyncio.run(walk_source(
            "longhu_limit_performance", [date(2026, 9, 18)], fetch,
            database=database, run_database=run_database, allowed=allowed, pace_seconds=0,
        ))
        retry = asyncio.run(walk_source(
            "longhu_limit_performance", [date(2026, 9, 18)], fetch,
            database=database, run_database=run_database, allowed=allowed, pace_seconds=0,
        ))
        self.assertEqual(report["empty"], 1)
        self.assertEqual(retry["empty"], 1)
        self.assertEqual(fetches, 2)
        self.assertEqual(database.inserts, [])

    def test_never_during_the_trading_session(self):
        self.assertFalse(backfill_allowed(datetime(2026, 9, 23, 10, 0, tzinfo=CN), True))
        self.assertTrue(backfill_allowed(datetime(2026, 9, 23, 16, 0, tzinfo=CN), True))
        self.assertTrue(backfill_allowed(datetime(2026, 9, 26, 10, 0, tzinfo=CN), False))


if __name__ == "__main__":
    unittest.main()
