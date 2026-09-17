import unittest

from app.health_read_model import async_pool_stall_reason


class AsyncPoolStallTests(unittest.TestCase):
    def test_a_pool_holding_nothing_while_callers_queue_is_stalled(self):
        # The exact shape the peer reported for 45 minutes while the container
        # kept answering /health with 200.
        reason = async_pool_stall_reason(
            {"open": True, "min_size": 1, "max_size": 8, "pool_size": 0, "available": 0, "waiting": 64}
        )
        self.assertIsNotNone(reason)
        self.assertIn("64", reason)

    def test_a_saturated_pool_is_not_stalled(self):
        # Every connection leased out with callers queued is ordinary load:
        # the pool still holds its connections.
        self.assertIsNone(
            async_pool_stall_reason(
                {"open": True, "min_size": 1, "max_size": 8, "pool_size": 8, "available": 0, "waiting": 12}
            )
        )

    def test_a_pool_still_filling_on_startup_is_not_stalled(self):
        self.assertIsNone(
            async_pool_stall_reason(
                {"open": True, "min_size": 1, "max_size": 8, "pool_size": 0, "available": 0, "waiting": 0}
            )
        )

    def test_a_healthy_idle_pool_is_not_stalled(self):
        self.assertIsNone(
            async_pool_stall_reason(
                {"open": True, "min_size": 1, "max_size": 8, "pool_size": 6, "available": 5, "waiting": 0}
            )
        )

    def test_a_closed_pool_is_not_reported_as_a_stall(self):
        # Shutdown is not a fault, and reporting one would fail the health
        # probe on every ordinary restart.
        self.assertIsNone(
            async_pool_stall_reason(
                {"open": False, "min_size": 1, "max_size": 8, "pool_size": 0, "available": 0, "waiting": 3}
            )
        )

    def test_a_runtime_without_an_async_pool_is_not_stalled(self):
        self.assertIsNone(async_pool_stall_reason(None))


if __name__ == "__main__":
    unittest.main()
