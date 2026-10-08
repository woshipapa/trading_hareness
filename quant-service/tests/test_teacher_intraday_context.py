"""The teacher plans' intraday context, moved out of main.py (docs/decisions/0008)."""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from app.teacher_intraday_context import TeacherContextDependencies, TeacherIntradayContext

# 09:30 Shanghai on a trading day
OPEN = datetime(2026, 10, 9, 1, 30, tzinfo=timezone.utc)


class FakeMarketBook:
    def __init__(self, missing):
        self.missing, self.auctions, self.sector_refreshed_at = list(missing), {}, None

    def auction_missing(self, symbols, _observed_at):
        return [symbol for symbol in self.missing if symbol in symbols]

    def store_auction(self, symbol, _observed_at, fact):
        self.auctions[symbol] = fact


class FakeTape:
    def __init__(self, loaded):
        self.loaded = list(loaded)

    def rehydrate(self, samples):
        return self.loaded.pop(0)


def context(*, now, fetch=None, run_database=None, book=None, tape=None, longhu=True):
    async def never(*_args, **_kwargs):
        raise AssertionError("not expected")

    deps = TeacherContextDependencies(
        database=object(), run_database=run_database or never, run_vendor_blocking=never,
        fetch_fuyao=fetch or never, longhu_configured=lambda: longhu, longhu_source=lambda: None,
        tencent_period_bars=never, safe_error=lambda value, limit: value[:limit], now=lambda: now[0],
    )
    ctx = TeacherIntradayContext(deps, market_book=book or FakeMarketBook([]), divergence_book=None,
                                 tape=tape or FakeTape([0]))
    return ctx


def plans(*pairs):
    return lambda _watches, _observed_at: [(symbol, {"levels": {"close": close}}) for symbol, close in pairs]


class AuctionTests(unittest.TestCase):
    def test_outside_the_auction_window_nothing_is_fetched(self):
        ctx = context(now=[OPEN - timedelta(minutes=10)])
        self.assertEqual(asyncio.run(ctx.refresh_auction([]))["status"], "outside_window")

    def test_only_a_final_snapshot_matching_yesterdays_close_is_stored_and_attempts_are_throttled(self):
        calls = []

        async def fetch(capability, params):
            calls.append((capability, params["thscodes"]))
            return {"data_status": "final", "item": [
                {"thscode": "000001.SZ", "pre_close_price": 10.0, "auction_price": 10.2, "auction_unmatched": 50,
                 "auction_amount": 1e6},
                {"thscode": "600000.SH", "pre_close_price": 7.0, "auction_price": 7.1},  # yesterday's row
            ]}

        now = [OPEN]
        book = FakeMarketBook(["000001.SZ", "600000.SH"])
        ctx = context(now=now, fetch=fetch, book=book)
        ctx.plans_today = plans(("000001.SZ", 10.0), ("600000.SH", 7.5))
        result = asyncio.run(ctx.refresh_auction([]))
        self.assertEqual((result["status"], result["stored"], result["rejected_count"]), ("completed", 1, 1))
        self.assertEqual(book.auctions["000001.SZ"]["seal_amount"], round(50 * 100 * 10.2, 2))
        self.assertNotIn("600000.SH", book.auctions)
        now[0] = OPEN + timedelta(seconds=10)
        self.assertEqual(asyncio.run(ctx.refresh_auction([]))["status"], "throttled")
        self.assertEqual(len(calls), 1)

    def test_a_vendor_error_is_reported_never_raised(self):
        async def fetch(_capability, _params):
            raise RuntimeError("fuyao down")

        ctx = context(now=[OPEN], fetch=fetch, book=FakeMarketBook(["000001.SZ"]))
        ctx.plans_today = plans(("000001.SZ", 10.0))
        self.assertEqual(asyncio.run(ctx.refresh_auction([])), {"status": "failed", "error": "fuyao down"})


class TapeTests(unittest.TestCase):
    def test_an_empty_reload_retries_after_the_throttle_and_a_loaded_one_settles_for_the_day(self):
        async def run_database(_read, **_kwargs):
            return [(OPEN, "000001.SZ", 10.0)]

        now = [OPEN]
        ctx = context(now=now, run_database=run_database, tape=FakeTape([0, 12]))
        self.assertEqual(asyncio.run(ctx.rehydrate_tape())["loaded"], 0)
        now[0] = OPEN + timedelta(seconds=30)
        self.assertEqual(asyncio.run(ctx.rehydrate_tape())["status"], "throttled")
        now[0] = OPEN + timedelta(seconds=61)
        self.assertEqual(asyncio.run(ctx.rehydrate_tape())["loaded"], 12)
        now[0] = OPEN + timedelta(minutes=5)
        self.assertEqual(asyncio.run(ctx.rehydrate_tape()), {"status": "done", "loaded": 12})


class DivergenceTests(unittest.TestCase):
    def test_without_longhu_divergence_is_disabled(self):
        ctx = context(now=[OPEN], longhu=False)
        self.assertEqual(asyncio.run(ctx.refresh_divergence([])), {"status": "disabled"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
