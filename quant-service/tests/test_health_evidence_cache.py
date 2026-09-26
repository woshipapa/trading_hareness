"""The slow health sections are served from memory; liveness stays live."""

from __future__ import annotations

import threading
import time
import unittest

from app.health_read_model import HealthEvidenceCache


class HealthEvidenceCacheTests(unittest.TestCase):
    def test_fresh_value_is_reused_without_recomputing(self):
        cache, calls = HealthEvidenceCache(ttl_seconds=30, max_stale_seconds=300), []
        compute = lambda: calls.append(1) or {"n": len(calls)}
        self.assertEqual(cache.get(compute)[0], {"n": 1})
        value, age = cache.get(compute)
        self.assertEqual((value, len(calls)), ({"n": 1}, 1))
        self.assertLess(age, 1)

    def test_a_stale_value_is_served_while_one_background_refresh_runs(self):
        cache = HealthEvidenceCache(ttl_seconds=0.01, max_stale_seconds=300)
        release, calls = threading.Event(), []

        def compute():
            calls.append(1)
            if len(calls) > 1:
                release.wait(2)
            return {"n": len(calls)}

        cache.get(compute)
        time.sleep(0.02)
        started = time.monotonic()
        self.assertEqual(cache.get(compute)[0], {"n": 1})   # stale value, no waiting on the slow refresh
        self.assertEqual(cache.get(compute)[0], {"n": 1})   # still one refresh in flight
        self.assertLess(time.monotonic() - started, 0.5)
        release.set()
        for _ in range(100):
            if cache.get(lambda: {"n": -1})[0] == {"n": 2}:
                break
            time.sleep(0.01)
        self.assertEqual(len(calls), 2)

    def test_a_value_older_than_the_stale_limit_is_recomputed_inline(self):
        cache, calls = HealthEvidenceCache(ttl_seconds=0.001, max_stale_seconds=0.01), []
        compute = lambda: calls.append(1) or {"n": len(calls)}
        cache.get(compute)
        time.sleep(0.02)
        self.assertEqual(cache.get(compute)[0], {"n": 2})


if __name__ == "__main__":
    unittest.main()
