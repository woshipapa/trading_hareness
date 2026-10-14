from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from app.auction_pool_runtime import (
    AUCTION_CAPABILITY,
    BID_CAPABILITY,
    CURVE_CAPABILITY,
    AuctionPoolCaptureDependencies,
    AuctionPoolCaptureRuntime,
    run_auction_pool_capture_loop,
)
from app.fuyao_provider import FuyaoProviderError
from app.public_provider_rate_limits import PublicProviderRateLimited


CN = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 10, 12)


class FakeClock:
    def __init__(self, current: datetime):
        self.current = current
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += timedelta(seconds=seconds)


class AuctionPoolRuntimeTests(unittest.TestCase):
    def dependencies(self, *, current=time(9, 14), pool=None, allowed=True, fetch=None, scored=None, table_stored=True):
        clock = FakeClock(datetime.combine(DAY, current, tzinfo=CN))
        persisted: list[tuple[str, str, list[dict]]] = []
        fuyao_calls: list[str] = []
        curve_calls: list[str] = []
        bid_calls: list[dict] = []
        pool = list(["000001.SZ"] if pool is None else pool)

        async def fetch_fuyao(_capability, params, *, slot_wait_seconds):
            fuyao_calls.append(params["thscodes"])
            if fetch:
                return await fetch(params["thscodes"], slot_wait_seconds)
            return {"item": [{"thscode": symbol, "data_status": "pending"} for symbol in params["thscodes"].split(",")]}

        async def fetch_curve(*, symbol):
            curve_calls.append(symbol)
            return [{"symbol": symbol, "at": "09:24:55"}]

        async def call_longhu(request):
            bid_calls.append(request)
            return {"pages": [{"payload": {"symbol": request["params"]["StockID"]}}]}

        async def storage_allowed():
            return allowed, {"allow_nonessential_high_frequency": allowed}

        def session_gate(_database, _at):
            return True, "open"

        async def run_database(action, *args, **_kwargs):
            if action is session_gate or action.__name__ == "session_gate":
                return action(*args)
            if action is read_pool:
                return action(*args)
            if action.__name__ == "persist_timed_observations":
                return persist(*args)
            return action(*args)

        def read_pool(_database, _date):
            return pool

        def persist(_database, provider, capability, rows):
            persisted.append((provider, capability, rows))
            return len(rows)

        deps = AuctionPoolCaptureDependencies(
            fetch_fuyao=fetch_fuyao, fetch_curve=fetch_curve, call_longhu=call_longhu,
            run_database=run_database, database=object(), storage_allowed=storage_allowed,
            read_pool=read_pool, now=lambda: clock.current, sleep=clock.sleep,
            session_gate=session_gate,
            read_scored_rows=lambda _database, _day: list(scored or []),
            read_table_stored=lambda _database, _day: table_stored,
        )
        return AuctionPoolCaptureRuntime(deps), clock, persisted, fuyao_calls, curve_calls, bid_calls

    def test_outside_storage_pause_and_no_pool(self):
        runtime, _clock, _persisted, _fuyao, _curves, _bids = self.dependencies(current=time(9, 0))
        self.assertEqual(asyncio.run(runtime.capture_session()), {"status": "outside_session"})
        runtime, _clock, _persisted, _fuyao, _curves, _bids = self.dependencies(allowed=False)
        self.assertEqual(asyncio.run(runtime.capture_session()), {
            "status": "storage_paused", "storage": {"allow_nonessential_high_frequency": False},
        })
        runtime, _clock, _persisted, _fuyao, _curves, _bids = self.dependencies(pool=[])
        self.assertEqual(asyncio.run(runtime.capture_session()), {"status": "no_pool"})

    def test_past_ticks_are_skipped_and_failures_do_not_stop_later_ticks(self):
        fetch_count = 0

        async def fetch(symbols, _wait):
            nonlocal fetch_count
            fetch_count += 1
            if fetch_count == 1:
                try:
                    raise PublicProviderRateLimited("fuyao_ths")
                except PublicProviderRateLimited as error:
                    raise FuyaoProviderError(str(error)) from error
            if fetch_count == 2:
                raise FuyaoProviderError("fuyao down")
            return {"item": [{"thscode": symbols, "data_status": "pending"}]}

        runtime, clock, persisted, calls, _curves, _bids = self.dependencies(current=time(9, 15, 2), fetch=fetch)
        ticks = tuple(datetime.combine(DAY, value, tzinfo=CN) for value in (time(9, 15, 0), time(9, 15, 5), time(9, 15, 10)))
        with patch("app.auction_pool_runtime.auction_timetable", return_value=ticks):
            result = asyncio.run(runtime.capture_session())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(calls, ["000001.SZ", "000001.SZ"])
        self.assertEqual([row[1] for row in persisted[:2]], [AUCTION_CAPABILITY, AUCTION_CAPABILITY])
        self.assertEqual(clock.sleeps[0], 3)

    def test_final_tick_stops_early_and_runs_curve_and_bid_passes(self):
        async def fetch(_symbols, slot_wait_seconds):
            self.assertEqual(slot_wait_seconds, 2)
            return {"data_status": "final", "item": [{"thscode": "000001.SZ"},
                                                         {"thscode": "000002.SZ"}]}

        runtime, clock, persisted, calls, curves, bids = self.dependencies(
            current=time(9, 14), pool=["000001.SZ", "000002.SZ"], fetch=fetch,
        )
        ticks = (datetime.combine(DAY, time(9, 15, 5), tzinfo=CN), datetime.combine(DAY, time(9, 15, 10), tzinfo=CN))
        with patch("app.auction_pool_runtime.auction_timetable", return_value=ticks):
            result = asyncio.run(runtime.capture_session())
        self.assertEqual(result["scheduled"], 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(curves, ["000001.SZ", "000002.SZ"])
        self.assertEqual(bids, [{"target": "longhu_quote", "path": "/w1/api/index.php", "params": {
            "a": "GetStockBid", "c": "StockL2Data", "PhoneOSNew": 1, "VerSion": "5.20.0.2",
            "apiv": "w41", "StockID": "000001",
        }}, {"target": "longhu_quote", "path": "/w1/api/index.php", "params": {
            "a": "GetStockBid", "c": "StockL2Data", "PhoneOSNew": 1, "VerSion": "5.20.0.2",
            "apiv": "w41", "StockID": "000002",
        }}])
        self.assertIn(CURVE_CAPABILITY, [capability for _provider, capability, _rows in persisted])
        self.assertIn(BID_CAPABILITY, [capability for _provider, capability, _rows in persisted])
        self.assertGreaterEqual(clock.current.time(), time(9, 30))

    def test_final_before_0925_does_not_stop_or_build_table(self):
        async def fetch(_symbols, _slot_wait_seconds):
            return {"data_status": "final", "item": [{"thscode": "000001.SZ"}]}

        runtime, _clock, _persisted, calls, _curves, _bids = self.dependencies(fetch=fetch)
        stored = AsyncMock()
        runtime._persist_table = stored
        tick = datetime.combine(DAY, time(9, 20, 5), tzinfo=CN)
        with patch("app.auction_pool_runtime.auction_timetable", return_value=(tick,)):
            result = asyncio.run(runtime.capture_session())
        self.assertEqual(result["scheduled"], 1)
        self.assertEqual(len(calls), 1)
        stored.assert_not_awaited()

    def test_restart_after_ticks_leaves_existing_table_untouched(self):
        runtime, _clock, _persisted, _calls, curves, bids = self.dependencies(current=time(9, 30))
        stored = AsyncMock()
        runtime._persist_table = stored
        result = asyncio.run(runtime.capture_session())
        self.assertEqual(result["scheduled"], 0)
        stored.assert_not_awaited()
        self.assertEqual(curves, ["000001.SZ"])
        self.assertEqual(len(bids), 1)

    def test_a_start_after_the_ticks_stores_the_final_auction_once_marked_late(self):
        scored = [{"symbol": "000001.SZ", "name": "A", "boards": 2, "risk_rank": 10.0, "sealed_from_open": False, "previous_close": 10.0}]

        async def final(symbols, _wait):
            return {"auction_phase": "closed", "data_status": "final", "item": [
                {"thscode": symbol, "open_price": 10.5, "auction_price": 10.5, "auction_amount": 5e6, "auction_volume": 4762,
                 "auction_unmatched": 100, "pre_close_price": 10.0, "last_price": 10.9} for symbol in symbols.split(",")]}

        runtime, _clock, persisted, calls, curves, bids = self.dependencies(current=time(11, 4), scored=scored, table_stored=False, fetch=final)
        stored = AsyncMock()
        runtime._persist_table = stored
        result = asyncio.run(runtime.capture_session())
        self.assertEqual((result["scheduled"], result["late_table"]), (0, "stored"))
        self.assertEqual(len(calls), 1)
        snapshot = next(rows[0] for _provider, capability, rows in persisted if capability == AUCTION_CAPABILITY)
        self.assertEqual((snapshot["status"], snapshot["scheduled_at"]), ("final", "2026-10-12T09:25:05+08:00"))
        self.assertEqual(snapshot["available_at"], snapshot["backfilled"]["fetched_at"])
        self.assertTrue(snapshot["available_at"].startswith("2026-10-12T03:04"), "the fetch time, in UTC: not 09:25")
        table = stored.await_args.args[0]
        self.assertEqual((len(table["rows"]), table["rows"][0]["open"], table["backfilled"]["fetched_at"]),
                         (1, 10.5, snapshot["available_at"]))
        self.assertEqual(curves, ["000001.SZ"], "the day's auction curve can still be read late")
        self.assertEqual((bids, result["bids"]["skipped_late"]), ([], 1), "a book taken at 11:04 is not the 09:30 book")

    def test_a_late_start_stores_nothing_when_the_table_exists_or_the_provider_is_not_final(self):
        scored = [{"symbol": "000001.SZ", "name": "A", "boards": 2, "risk_rank": 10.0, "sealed_from_open": False, "previous_close": 10.0}]
        for table_stored, expected, fetches in ((True, "already_stored", 0), (False, "not_final", 1)):
            runtime, _clock, persisted, calls, _curves, _bids = self.dependencies(current=time(10, 0), scored=scored, table_stored=table_stored)
            stored = AsyncMock()
            runtime._persist_table = stored
            result = asyncio.run(runtime.capture_session())
            with self.subTest(expected=expected):
                self.assertEqual((result["late_table"], len(calls)), (expected, fetches))
                stored.assert_not_awaited()
                self.assertNotIn(AUCTION_CAPABILITY, [capability for _provider, capability, _rows in persisted])

    def test_duplicate_final_row_does_not_hide_a_missing_pool_symbol(self):
        async def fetch(_symbols, _slot_wait_seconds):
            return {"data_status": "final", "item": [
                {"thscode": "000001.SZ"}, {"thscode": "000001.SZ"}, {"thscode": "600000.SH"},
            ]}

        runtime, _clock, _persisted, calls, _curves, _bids = self.dependencies(
            pool=["000001.SZ", "000002.SZ"], fetch=fetch,
        )
        ticks = (datetime.combine(DAY, time(9, 15, 5), tzinfo=CN),
                 datetime.combine(DAY, time(9, 15, 10), tzinfo=CN))
        with patch("app.auction_pool_runtime.auction_timetable", return_value=ticks):
            result = asyncio.run(runtime.capture_session())
        self.assertEqual(result["scheduled"], 2)
        self.assertEqual(len(calls), 2)

    def test_curve_pass_is_locally_paced_without_the_provider_limiter(self):
        runtime, clock, _persisted, _fuyao, curves, _bids = self.dependencies(
            pool=["000001.SZ", "000002.SZ"],
        )
        with patch("app.public_provider_rate_limits.acquire_public_provider_slot", new=AsyncMock()) as limiter:
            result = asyncio.run(runtime._capture_curves(DAY, ["000001.SZ", "000002.SZ"]))
        self.assertEqual(result, {"stored": 2, "failed": 0})
        self.assertEqual(curves, ["000001.SZ", "000002.SZ"])
        self.assertEqual(clock.sleeps, [1])
        limiter.assert_not_awaited()

    def test_outer_loop_retries_database_busy_and_uses_post_session_clock(self):
        runtime, clock, _persisted, _fuyao, _curves, _bids = self.dependencies()
        capture = AsyncMock(side_effect=[{"status": "database_busy"}, {"status": "completed"}])
        runtime.capture_session = capture
        clock.current = datetime.combine(DAY, time(9, 14), tzinfo=CN)

        class StopLoop(Exception):
            pass

        async def retry_then_stop(seconds):
            clock.sleeps.append(seconds)
            if len(clock.sleeps) == 1:
                clock.current += timedelta(seconds=seconds)
                return
            raise StopLoop

        object.__setattr__(runtime.deps, "sleep", retry_then_stop)
        with self.assertRaises(StopLoop):
            asyncio.run(run_auction_pool_capture_loop(runtime))
        self.assertEqual(capture.await_count, 2)
        self.assertEqual(clock.sleeps[0], 5)

        clock.current = datetime.combine(DAY, time(9, 30), tzinfo=CN)
        runtime.capture_session = AsyncMock(return_value={"status": "completed"})
        async def stop_next(seconds):
            clock.sleeps.append(seconds)
            raise StopLoop
        object.__setattr__(runtime.deps, "sleep", stop_next)
        with self.assertRaises(StopLoop):
            asyncio.run(run_auction_pool_capture_loop(runtime))
        self.assertEqual(clock.sleeps[-1], 23 * 3600 + 44 * 60)

    def _loop_until_next_day(self, runtime, clock):
        """Run the outer loop once through a session; it stops at the sleep to the next 09:14."""
        class StopLoop(Exception):
            pass

        async def sleep(seconds):
            clock.sleeps.append(seconds)
            if seconds > 3600:
                raise StopLoop
            clock.current += timedelta(seconds=seconds)

        object.__setattr__(runtime.deps, "sleep", sleep)
        with self.assertRaises(StopLoop):
            asyncio.run(run_auction_pool_capture_loop(runtime))

    def test_outer_loop_retries_a_late_table_the_provider_did_not_give_final(self):
        scored = [{"symbol": "000001.SZ", "name": "A", "boards": 2, "risk_rank": 10.0, "sealed_from_open": False, "previous_close": 10.0}]
        reads = []

        async def fetch(symbols, _wait):
            reads.append(symbols)
            if len(reads) == 1:
                return {"auction_phase": "closed", "data_status": "pending", "item": []}
            return {"auction_phase": "closed", "data_status": "final", "item": [
                {"thscode": symbol, "open_price": 10.5, "auction_price": 10.5, "auction_amount": 5e6, "auction_volume": 4762,
                 "auction_unmatched": 100, "pre_close_price": 10.0, "last_price": 10.9} for symbol in symbols.split(",")]}

        runtime, clock, _persisted, _calls, _curves, _bids = self.dependencies(
            current=time(9, 26), scored=scored, table_stored=False, fetch=fetch)
        stored = AsyncMock()
        runtime._persist_table = stored
        self._loop_until_next_day(runtime, clock)
        self.assertEqual(len(reads), 2, "one read at the start, one after the minute")
        self.assertEqual(stored.await_count, 1)
        self.assertIn(60, clock.sleeps)
        self.assertGreater(clock.sleeps[-1], 3600, "then the loop sleeps to the next session")

    def test_late_table_retries_stop_at_the_limit_and_at_the_end_of_the_morning(self):
        from app import auction_pool_runtime

        scored = [{"symbol": "000001.SZ", "name": "A", "boards": 2, "risk_rank": 10.0, "sealed_from_open": False, "previous_close": 10.0}]
        reads = []

        async def pending(symbols, _wait):
            reads.append(symbols)
            return {"auction_phase": "closed", "data_status": "pending", "item": []}

        runtime, clock, _persisted, _calls, _curves, _bids = self.dependencies(
            current=time(9, 26), scored=scored, table_stored=False, fetch=pending)
        self._loop_until_next_day(runtime, clock)
        self.assertEqual(len(reads), 1 + auction_pool_runtime.LATE_TABLE_RETRY_LIMIT)

        reads.clear()
        runtime, clock, _persisted, _calls, _curves, _bids = self.dependencies(
            current=time(11, 29, 30), scored=scored, table_stored=False, fetch=pending)
        self._loop_until_next_day(runtime, clock)
        self.assertEqual(len(reads), 1, "a retry a minute later would be past 11:30: none is made")

    def test_outer_loop_waits_for_a_pool_that_is_built_after_the_session_opens(self):
        runtime, clock, _persisted, _fuyao, _curves, _bids = self.dependencies()
        clock.current = datetime.combine(DAY, time(9, 20), tzinfo=CN)
        capture = AsyncMock(side_effect=[{"status": "no_pool"}, {"status": "completed"}])
        runtime.capture_session = capture
        self._loop_until_next_day(runtime, clock)
        self.assertEqual(capture.await_count, 2)
        self.assertEqual(clock.sleeps[0], 30)

        clock.current = datetime.combine(DAY, time(9, 31), tzinfo=CN)
        clock.sleeps.clear()
        capture = AsyncMock(return_value={"status": "no_pool"})
        runtime.capture_session = capture
        self._loop_until_next_day(runtime, clock)
        self.assertEqual(capture.await_count, 1, "after 09:30 an empty pool is not retried")


class AuctionTrackCalendarTest(unittest.TestCase):
    def test_track_counts_sessions_back_from_today(self):
        from app import auction_pool_runtime

        class Connection:
            def __init__(self):
                self.calls = []

            def execute(self, sql, params):
                self.calls.append((sql, params))
                return self

            def fetchall(self):
                return []

        class Database:
            def __init__(self):
                self.connection = Connection()

            def transaction(self):
                database = self

                class Tx:
                    def __enter__(self):
                        return database.connection

                    def __exit__(self, *_args):
                        return False

                return Tx()

        database = Database()
        self.assertEqual(auction_pool_runtime.track(database, 5)["days"], [])
        sql, params = database.connection.calls[0]
        self.assertIn("calendar_date <= %s", sql)
        self.assertEqual(params, (datetime.now(CN).date(), 5))


if __name__ == "__main__":
    unittest.main()
