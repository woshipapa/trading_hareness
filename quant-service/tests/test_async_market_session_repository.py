from __future__ import annotations

from datetime import datetime, timezone
import unittest

from app.async_market_session_repository import (
    market_observation_session,
    realtime_market_session,
    sse_calendar_open,
    sse_calendar_status,
)


class _Result:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, row=None, error: Exception | None = None):
        self.row = row
        self.error = error
        self.calls = []

    async def execute(self, query, params):
        self.calls.append((query, params))
        if self.error:
            raise self.error
        return _Result(self.row)


class _Transaction:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_):
        return False


class _Database:
    def __init__(self, row=None, error=None):
        self.connection = _Connection(row, error)

    def transaction(self):
        return _Transaction(self.connection)


class AsyncMarketSessionRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_calendar_is_open_only_for_a_persisted_open_exchange_day(self) -> None:
        from datetime import date
        database = _Database({"is_open": True})
        self.assertEqual(await sse_calendar_status(database, date(2026, 8, 13)), (True, "SSE trade calendar marks today open"))
        self.assertTrue(await sse_calendar_open(database, date(2026, 8, 13)))
        self.assertEqual(database.connection.calls[0][1], (date(2026, 8, 13),))

    async def test_native_calendar_fails_closed_for_pool_error_and_realtime_gate_reuses_it(self) -> None:
        from datetime import date
        offline = _Database(error=RuntimeError("pool unavailable"))
        status = await sse_calendar_status(offline, date(2026, 8, 13))
        self.assertFalse(status[0])
        self.assertIn("fail closed", status[1])
        active, reason = await realtime_market_session(
            offline, now=datetime(2026, 8, 13, 2, tzinfo=timezone.utc),
        )
        self.assertFalse(active)
        self.assertIn("fail closed", reason)

    async def test_observation_gate_opens_at_0915_but_strategy_gate_stays_closed(self) -> None:
        china = timezone(__import__("datetime").timedelta(hours=8))
        observed_at = datetime(2026, 9, 21, 9, 15, tzinfo=china)
        database = _Database({"is_open": True})
        self.assertTrue((await market_observation_session(database, observed_at))[0])
        self.assertFalse((await realtime_market_session(database, now=observed_at))[0])

    async def test_observation_gate_honors_calendar_and_clock(self) -> None:
        china = timezone(__import__("datetime").timedelta(hours=8))
        self.assertFalse((await market_observation_session(
            _Database({"is_open": True}), datetime(2026, 9, 21, 9, 14, tzinfo=china),
        ))[0])
        self.assertFalse((await market_observation_session(
            _Database({"is_open": False}), datetime(2026, 9, 21, 9, 15, tzinfo=china),
        ))[0])


if __name__ == "__main__":
    unittest.main()
