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
    def dependencies(self, *, current=time(9, 14), pool=None, allowed=True, fetch=None):
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
        self.assertEqual(result["scheduled"], 1)
        self.assertEqual(len(calls), 1)
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


if __name__ == "__main__":
    unittest.main()
