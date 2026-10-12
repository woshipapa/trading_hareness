from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.fuyao_provider import FUYAO_SLOT_MAX_WAIT_SECONDS, FuyaoProviderError, fetch_envelope
from app.public_provider_rate_limits import PublicProviderRateLimited
from app.runtime_executors import run_auction_capture_blocking, runtime_executor_status


class AuctionOpenDeskProviderTests(unittest.TestCase):
    def _response_client(self) -> tuple[MagicMock, object]:
        response = MagicMock(status_code=200, headers={})
        response.raise_for_status.return_value = None
        response.json.return_value = {"code": 0, "message": "success", "data": {"item": []}}
        client = MagicMock()
        client.get = AsyncMock(return_value=response)

        @asynccontextmanager
        async def fake_client(*_args: object, **_kwargs: object):
            yield client

        return client, fake_client

    def test_default_slot_wait_is_preserved(self) -> None:
        _client, fake_client = self._response_client()
        acquire = AsyncMock()

        async def run() -> None:
            with patch.dict("os.environ", {"HITHINK_FINANCE_API_KEY": "fixture"}, clear=True), \
                 patch("app.fuyao_provider.provider_http_client", new=fake_client), \
                 patch("app.fuyao_provider.acquire_public_provider_slot", new=acquire):
                await fetch_envelope("a_share_trading_days", {})

        asyncio.run(run())
        acquire.assert_awaited_once_with("fuyao_ths", max_wait_seconds=FUYAO_SLOT_MAX_WAIT_SECONDS)

    def test_short_slot_wait_surfaces_limiter_refusal(self) -> None:
        _client, fake_client = self._response_client()

        async def run() -> None:
            with patch.dict("os.environ", {"HITHINK_FINANCE_API_KEY": "fixture"}, clear=True), \
                 patch("app.fuyao_provider.provider_http_client", new=fake_client), \
                 patch("app.fuyao_provider.acquire_public_provider_slot",
                       new=AsyncMock(side_effect=PublicProviderRateLimited("fuyao_ths"))):
                with self.assertRaisesRegex(FuyaoProviderError, "rate_limited:fuyao_ths"):
                    await fetch_envelope("a_share_trading_days", {}, slot_wait_seconds=0.1)

        asyncio.run(run())


class AuctionCaptureExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_boundary_is_isolated_and_visible(self) -> None:
        self.assertEqual(await run_auction_capture_blocking(lambda value: value + 1, 1, timeout_seconds=1), 2)
        status = runtime_executor_status()
        self.assertIn("auction_capture", status)
        self.assertEqual(status["auction_capture"]["workers"], 1)
        self.assertEqual(status["auction_capture"]["queue_capacity"], 0)
