from __future__ import annotations

import asyncio
import unittest

from app.async_keyed_memo import AsyncKeyedMemo


class AsyncKeyedMemoTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_same_key_joins_and_success_is_reused(self) -> None:
        calls: list[str] = []
        gate = asyncio.Event()

        async def fetch(key: str) -> str:
            calls.append(key)
            await gate.wait()
            return f"value:{key}"

        memo = AsyncKeyedMemo(fetch, ttl_seconds=60)
        first = asyncio.create_task(memo.get("close"))
        second = asyncio.create_task(memo.get("close"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(calls, ["close"])
        gate.set()
        self.assertEqual(await asyncio.gather(first, second), ["value:close", "value:close"])
        self.assertEqual(await memo.get("close"), "value:close")
        self.assertEqual(calls, ["close"])

    async def test_failed_result_is_retryable(self) -> None:
        calls = 0

        async def fetch(_key: str) -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("temporary")
            return "ok"

        memo = AsyncKeyedMemo(fetch, ttl_seconds=60)
        with self.assertRaisesRegex(RuntimeError, "temporary"):
            await memo.get("close")
        self.assertEqual(await memo.get("close"), "ok")
        self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
