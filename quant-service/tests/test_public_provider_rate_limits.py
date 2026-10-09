import asyncio
import os
import unittest
from unittest.mock import patch

from app.public_provider_rate_limits import (
    PublicProviderRateLimited,
    PublicProviderRateLimiter,
    configured_rate_limit,
    provider_key_for_host,
)


class PublicProviderRateLimitTests(unittest.TestCase):
    def test_provider_host_mapping_is_explicit(self):
        self.assertEqual(provider_key_for_host("push2.eastmoney.com"), "eastmoney_free")
        self.assertEqual(provider_key_for_host("qt.gtimg.cn"), "tencent_free")
        self.assertEqual(provider_key_for_host("hq.sinajs.cn"), "sina_free")

    def test_second_request_is_rejected_without_waiting(self):
        limiter = PublicProviderRateLimiter()

        async def run():
            self.assertTrue(await limiter.try_acquire("eastmoney_free", 60))
            with self.assertRaises(PublicProviderRateLimited):
                await limiter.acquire("eastmoney_free", 60)

        asyncio.run(run())

    def test_a_bounded_wait_queues_a_burst_at_the_same_pacing(self):
        limiter = PublicProviderRateLimiter()
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(round(seconds, 1))

        async def run():
            with patch("app.public_provider_rate_limits.asyncio.sleep", fake_sleep):
                # Two pages back to back, then a third caller in the same instant.
                for _ in range(3):
                    await limiter.acquire("fuyao_ths", 60, max_wait_seconds=15)

        asyncio.run(run())
        self.assertEqual(len(sleeps), 2, "the first slot is free; the next two wait their turn")
        self.assertAlmostEqual(sleeps[0], 1.0, places=1)
        self.assertAlmostEqual(sleeps[1], 2.0, places=1)

    def test_a_slot_beyond_the_bound_is_still_refused(self):
        limiter = PublicProviderRateLimiter()

        async def run():
            await limiter.acquire("fuyao_ths", 6, max_wait_seconds=5)
            with self.assertRaises(PublicProviderRateLimited):
                # The next slot is ten seconds off, beyond the five allowed.
                await limiter.acquire("fuyao_ths", 6, max_wait_seconds=5)

        asyncio.run(run())

    def test_fuyao_waits_for_its_slot_instead_of_failing(self):
        from app import fuyao_provider

        self.assertGreater(fuyao_provider.FUYAO_SLOT_MAX_WAIT_SECONDS, 2.0,
                           "long enough for the all-A snapshot's second page")

    def test_rate_limit_can_be_configured_without_secrets(self):
        with patch.dict(os.environ, {"PUBLIC_RATE_LIMIT_SINA_FREE_PER_MINUTE": "17"}, clear=False):
            self.assertEqual(configured_rate_limit("sina_free"), 17)


if __name__ == "__main__":
    unittest.main()
