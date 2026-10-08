"""The 60-second evidence capture's wiring, moved out of main.py (docs/decisions/0008)."""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app import market_event_runtime
from app.market_event_runtime import MarketEventCaptureDependencies, run_market_event_capture_service

NOW = datetime(2026, 10, 9, 1, 30, tzinfo=timezone.utc)


def deps(*, longhu=False, snapshot_error=None):
    async def run(action, *args, **_kwargs):
        return action(*args)

    async def snapshot():
        if snapshot_error:
            raise snapshot_error
        return [{"symbol": "000001.SZ"}], {}

    async def never(*_args, **_kwargs):
        raise AssertionError("not expected")

    async def session(_now):
        return True, "open"

    return MarketEventCaptureDependencies(
        fetch_fuyao=never, run_database=run, database=object(), persist_market_events=lambda *_args: 0,
        persist_timed_observations=lambda *_args: 0, session_open=session, all_a_snapshot=snapshot,
        universe_symbols=lambda: ["600000.SH"], health_capability=lambda *args, **kwargs: "x",
        record_success=lambda *_args: None, record_failure=lambda *_args: None,
        longhu_configured=lambda: longhu, vendor_call=never, capture_events=never, capture_longhu_auction=never,
    )


class MarketEventCaptureServiceTests(unittest.TestCase):
    def wired(self, service_deps):
        captured = {}

        async def fake_loop(**kwargs):
            captured.update(kwargs)

        with patch.object(market_event_runtime, "run_market_event_capture_loop", fake_loop):
            asyncio.run(run_market_event_capture_service(service_deps))
        return captured

    def test_the_longhu_leg_is_skipped_without_the_vendor(self):
        loop = self.wired(deps(longhu=False))
        self.assertEqual(asyncio.run(loop["capture_longhu_auction"](NOW))["reason"], "longhu_not_configured")
        self.assertTrue(asyncio.run(loop["session_open"](NOW)))
        self.assertEqual(loop["interval_seconds"], 60)

    def test_the_symbol_list_prefers_fuyao_and_falls_back_to_the_local_universe(self):
        self.assertEqual(asyncio.run(self.wired(deps())["symbols"]()), ["000001.SZ"])
        fallback = self.wired(deps(snapshot_error=RuntimeError("fuyao down")))
        self.assertEqual(asyncio.run(fallback["symbols"]()), ["600000.SH"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
