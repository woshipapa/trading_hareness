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

    def test_rate_limit_can_be_configured_without_secrets(self):
        with patch.dict(os.environ, {"PUBLIC_RATE_LIMIT_SINA_FREE_PER_MINUTE": "17"}, clear=False):
            self.assertEqual(configured_rate_limit("sina_free"), 17)


if __name__ == "__main__":
    unittest.main()
