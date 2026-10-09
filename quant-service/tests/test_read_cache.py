"""The heavy-read cache: one computation per key while fresh, shared by concurrent callers."""

from __future__ import annotations

import asyncio
import unittest

from app.read_cache import TTLCache


class TTLCacheTests(unittest.TestCase):
    def test_a_fresh_entry_is_reused_and_an_old_one_recomputed(self):
        now = [0.0]
        cache = TTLCache(120.0, clock=lambda: now[0])
        calls = []

        async def compute():
            calls.append(now[0])
            return {"n": len(calls)}

        async def scenario():
            first = await cache.get("k", compute)
            now[0] = 60.0
            second = await cache.get("k", compute)
            now[0] = 121.0
            third = await cache.get("k", compute)
            return first, second, third

        first, second, third = asyncio.run(scenario())
        self.assertEqual((first, second, third), ({"n": 1}, {"n": 1}, {"n": 2}))

    def test_concurrent_callers_share_one_computation(self):
        cache = TTLCache(120.0)
        calls = []

        async def compute():
            calls.append(1)
            await asyncio.sleep(0.01)
            return "value"

        async def scenario():
            return await asyncio.gather(*(cache.get("k", compute) for _ in range(5)))

        self.assertEqual(asyncio.run(scenario()), ["value"] * 5)
        self.assertEqual(len(calls), 1)

    def test_a_failure_is_not_cached(self):
        cache = TTLCache(120.0)
        attempts = []

        async def flaky():
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("timeout")
            return "ok"

        async def scenario():
            with self.assertRaises(RuntimeError):
                await cache.get("k", flaky)
            return await cache.get("k", flaky)

        self.assertEqual(asyncio.run(scenario()), "ok")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
