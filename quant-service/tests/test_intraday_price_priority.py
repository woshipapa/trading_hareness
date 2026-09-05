import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from app.intraday_price_priority import fresh_price_rows, primary_order_books
from app.intraday_quote_normalization import exchange_time_status, merge_longhu_watch_quotes


NOW = datetime(2026, 9, 7, 1, 30, 10, tzinfo=timezone.utc)


def row(symbol="000001.SZ", stamp="20260907093009", price=10):
    return {"ts_code": symbol, "trade_time": stamp, "price": price,
            "bids": [{"price": 10, "size": 20}], "asks": []}


class PricePriorityTests(unittest.IsolatedAsyncioTestCase):
    def test_only_fresh_valid_requested_prices_can_win(self):
        rows = [row(), row("000002.SZ", "20260904150000"),
                row("000003.SZ", None), row("000004.SZ", price=0), row("000005.SZ")]
        accepted, rejected = fresh_price_rows(
            rows, symbols=["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"],
            merge=lambda quotes, values: merge_longhu_watch_quotes(quotes, values, number=lambda v: float(v) if v is not None else None),
            freshness=exchange_time_status, observed_at=NOW, max_age_seconds=20,
        )
        self.assertEqual(accepted, [rows[0]])
        self.assertEqual(rejected["000002.SZ"], "stale_timestamp")
        self.assertEqual(rejected["000003.SZ"], "missing_timestamp")
        self.assertEqual(rejected["000004.SZ"], "invalid_price")

    async def test_depth_primary_keeps_one_sided_and_falls_back_only_for_missing_or_stale(self):
        licensed = AsyncMock(return_value=[row(), row("000002.SZ", "20260904150000")])
        public = AsyncMock(return_value=[row("000002.SZ"), row("000003.SZ")])
        rows = await primary_order_books(
            ["000001.SZ", "000002.SZ", "000003.SZ"], max_symbols=3,
            licensed=licensed, fallback=public, now=lambda: NOW,
        )
        public.assert_awaited_once_with(["000002.SZ", "000003.SZ"], max_symbols=2)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["asks"], [])

    async def test_fallback_failure_does_not_discard_good_primary_rows(self):
        rows = await primary_order_books(
            ["000001.SZ", "000002.SZ"], max_symbols=2,
            licensed=AsyncMock(return_value=[row()]), fallback=AsyncMock(side_effect=RuntimeError("offline")),
            now=lambda: NOW,
        )
        self.assertEqual(len(rows), 1)

    async def test_primary_outage_uses_fallback_but_never_old_books(self):
        rows = await primary_order_books(
            ["000001.SZ", "000002.SZ"], max_symbols=2,
            licensed=AsyncMock(side_effect=RuntimeError("offline")),
            fallback=AsyncMock(return_value=[row(), row("000002.SZ", "20260904150000")]), now=lambda: NOW,
        )
        self.assertEqual([r["ts_code"] for r in rows], ["000001.SZ"])
