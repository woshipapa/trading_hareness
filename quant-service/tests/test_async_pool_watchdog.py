import asyncio
import unittest
from datetime import datetime, timezone

from app.async_pool_watchdog import (
    WATCHDOG_STALL_CONFIRMATIONS,
    AsyncPoolWatchdogState,
    check_once,
    watchdog_loop,
)

HEALTHY = {"open": True, "min_size": 1, "max_size": 8, "pool_size": 6, "available": 5, "waiting": 0}
STALLED = {"open": True, "min_size": 1, "max_size": 8, "pool_size": 0, "available": 0, "waiting": 64}


class _Pool:
    """Reports a scripted sequence of statuses and records replacements."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.replacements = 0

    def status(self):
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    async def replace(self):
        self.replacements += 1


class CheckOnceTests(unittest.TestCase):
    def test_a_healthy_pool_is_left_alone(self):
        pool, state = _Pool([HEALTHY]), AsyncPoolWatchdogState()
        self.assertIsNone(asyncio.run(check_once(state, pool.status, pool.replace)))
        self.assertEqual(pool.replacements, 0)

    def test_one_stalled_observation_does_not_replace_the_pool(self):
        # A pool briefly empty while its first connections are established
        # must not be torn down for it.
        pool, state = _Pool([STALLED]), AsyncPoolWatchdogState()
        self.assertIsNone(asyncio.run(check_once(state, pool.status, pool.replace)))
        self.assertEqual(pool.replacements, 0)
        self.assertEqual(state.consecutive_stalls, 1)

    def test_a_confirmed_stall_replaces_the_pool_once(self):
        pool, state = _Pool([STALLED]), AsyncPoolWatchdogState()

        async def run():
            return [await check_once(state, pool.status, pool.replace)
                    for _ in range(WATCHDOG_STALL_CONFIRMATIONS)]

        results = asyncio.run(run())
        self.assertEqual(results[:-1], [None] * (WATCHDOG_STALL_CONFIRMATIONS - 1))
        self.assertIsNotNone(results[-1])
        self.assertEqual(pool.replacements, 1)
        self.assertEqual(state.recoveries, 1)
        self.assertEqual(state.consecutive_stalls, 0)

    def test_a_healthy_observation_resets_the_streak(self):
        # An intermittent stall must not accumulate its way to a replacement
        # across unrelated minutes.
        pool = _Pool([STALLED, STALLED, HEALTHY, STALLED, STALLED, STALLED])
        state = AsyncPoolWatchdogState()

        async def run():
            for _ in range(5):
                await check_once(state, pool.status, pool.replace)

        asyncio.run(run())
        self.assertEqual(pool.replacements, 0)

    def test_a_recovery_is_timestamped_for_the_health_payload(self):
        pool, state = _Pool([STALLED]), AsyncPoolWatchdogState()
        moment = datetime(2026, 9, 17, 9, 1, tzinfo=timezone.utc)

        async def run():
            for _ in range(WATCHDOG_STALL_CONFIRMATIONS):
                await check_once(state, pool.status, pool.replace, now=lambda: moment)

        asyncio.run(run())
        snapshot = state.snapshot()
        self.assertEqual(snapshot["last_recovered_at"], moment.isoformat())
        self.assertEqual(snapshot["recoveries"], 1)
        self.assertIn("64", snapshot["last_reason"])

    def test_a_pool_that_stays_stalled_is_replaced_again_later(self):
        # Replacing once is not a promise that it worked; a tunnel still down
        # leaves the new pool empty too, and the loop must keep trying.
        pool, state = _Pool([STALLED]), AsyncPoolWatchdogState()

        async def run():
            for _ in range(WATCHDOG_STALL_CONFIRMATIONS * 2):
                await check_once(state, pool.status, pool.replace)

        asyncio.run(run())
        self.assertEqual(pool.replacements, 2)


class WatchdogLoopTests(unittest.TestCase):
    def test_the_loop_waits_before_its_first_observation(self):
        # Startup has just opened the pool; checking immediately would read it
        # mid-fill and start a streak against a pool that is simply new.
        pool, state = _Pool([HEALTHY]), AsyncPoolWatchdogState()
        delays: list[float] = []

        async def sleep(seconds: float) -> None:
            delays.append(seconds)
            if len(delays) >= 2:
                raise asyncio.CancelledError

        async def run():
            with self.assertRaises(asyncio.CancelledError):
                await watchdog_loop(state, pool.status, pool.replace, interval_seconds=30.0, sleep=sleep)

        asyncio.run(run())
        self.assertEqual(delays, [30.0, 30.0])


if __name__ == "__main__":
    unittest.main()
