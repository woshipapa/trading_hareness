from __future__ import annotations

import asyncio
import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.intraday_minute_capture_actions import (
    fetch_longhu_first_minute_rows, minute_row_datetime, minute_storage_source,
)


class IntradayMinuteCaptureActionTests(unittest.TestCase):
    def test_provider_date_is_not_relabelled_as_current_session(self) -> None:
        trading_date = date(2026, 9, 1)
        self.assertIsNone(minute_row_datetime(
            {"trade_time": "2026-08-31 14:59:00", "time": "14:59"}, trading_date,
        ))
        parsed = minute_row_datetime(
            {"trade_time": "2026-09-01 14:59:00", "time": "14:59"}, trading_date,
        )
        self.assertEqual(parsed.strftime("%Y-%m-%d %H:%M"), "2026-09-01 14:59")

    def test_hhmm_only_public_row_uses_requested_session(self) -> None:
        parsed = minute_row_datetime({"time": "1459"}, date(2026, 9, 1))
        self.assertEqual(parsed.strftime("%Y-%m-%d %H:%M"), "2026-09-01 14:59")

    def test_explicit_trade_date_is_checked_before_session_relabel(self) -> None:
        self.assertIsNone(minute_row_datetime(
            {"trade_date": "20260831", "time": "1459"}, date(2026, 9, 1),
        ))
        parsed = minute_row_datetime(
            {"trade_date": "20260901", "time": "1459"}, date(2026, 9, 1),
        )
        self.assertEqual(parsed.strftime("%Y-%m-%d %H:%M"), "2026-09-01 14:59")

    def test_storage_source_is_allowlisted(self) -> None:
        self.assertEqual(minute_storage_source({"storage_source": "longhu_intraday_minutes"}), "longhu_intraday_minutes")
        self.assertEqual(minute_storage_source({"storage_source": "untrusted"}), "tencent_intraday_minutes")

    def test_longhu_first_fetch_falls_back_when_date_is_missing(self) -> None:
        async def longhu(_symbol: str):
            return [{"time": "09:30", "close": 10}]

        async def fallback(_symbol: str):
            return [{"time": "09:30", "close": 10}]

        rows, source = asyncio.run(fetch_longhu_first_minute_rows(
            "600664.SH", observed_at=datetime(2026, 9, 1, 1, 30, tzinfo=timezone.utc),
            longhu_fetch=longhu, fallback_fetch=fallback,
        ))
        self.assertEqual(rows[0]["time"], "09:30")
        self.assertEqual(source["storage_source"], "tencent_intraday_minutes")
        self.assertEqual(source["status"], "fallback")

    def test_empty_longhu_fetch_is_a_fallback(self) -> None:
        async def longhu(_symbol: str):
            return []

        async def fallback(_symbol: str):
            return [{"time": "09:30", "close": 10}]

        rows, source = asyncio.run(fetch_longhu_first_minute_rows(
            "600664.SH", observed_at=datetime(2026, 9, 1, 1, 30, tzinfo=timezone.utc),
            longhu_fetch=longhu, fallback_fetch=fallback,
        ))
        self.assertEqual(rows[0]["time"], "09:30")
        self.assertEqual(source["status"], "fallback")

    def test_capture_persists_longhu_source_and_skips_cross_date_rows(self) -> None:
        today = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).date()
        yesterday = today - timedelta(days=1)

        class Connection:
            def __init__(self):
                self.calls = []

            def execute(self, sql, params=()):
                self.calls.append((sql, params))

        connection = Connection()

        class Database:
            pass

        # A small context manager keeps this test at the repository boundary.
        class Tx:
            def __enter__(self):
                return connection

            def __exit__(self, *_args):
                return False

        Database.transaction = lambda _self: Tx()
        action = __import__("app.intraday_minute_capture_actions", fromlist=["IntradayMinuteCaptureActions"]).IntradayMinuteCaptureActions(Database())

        async def run_database(fn, **_kwargs):
            return fn()

        async def fetch_source(_symbol: str):
            return [
                {"trade_date": today.strftime("%Y%m%d"), "time": "09:30", "close": 10, "vol": 1, "amount": 100},
                {"trade_date": yesterday.strftime("%Y%m%d"), "time": "09:31", "close": 10, "vol": 1, "amount": 100},
            ], {"storage_source": "longhu_intraday_minutes", "provider": "longhuvip"}

        result = asyncio.run(action.capture(
            ["600664.SH"], realtime_session=lambda: asyncio.sleep(0, result=(True, "open")),
            fetch_minutes=lambda _symbol: asyncio.sleep(0, result=[]), fetch_minutes_with_source=fetch_source,
            run_database=run_database, parse_minute=lambda row: {
                "bar_time": datetime.fromisoformat(row["datetime"]).replace(tzinfo=ZoneInfo("Asia/Shanghai")),
                "open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"],
                "volume": row.get("vol", 1), "amount": row.get("amount", 100), "raw": row,
            }, ensure_instrument=lambda *_args: None, retention_days=lambda: 7,
        ))
        self.assertEqual(result["stored"], 1)
        insert_calls = [item for item in connection.calls if "INSERT INTO quant.intraday_minute_sessions" in item[0]]
        self.assertEqual(len(insert_calls), 1)
        self.assertEqual(insert_calls[0][1][10], "longhu_intraday_minutes")
        delete_sql = next(sql for sql, _params in connection.calls if "DELETE FROM quant.intraday_minute_sessions" in sql)
        self.assertIn("longhu_intraday_minutes", delete_sql)


if __name__ == "__main__":
    unittest.main()
