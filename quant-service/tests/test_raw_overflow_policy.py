import unittest

from app.raw_overflow_policy import RawOverflowLimits, batch_allowed, classify


class RawOverflowPolicyTests(unittest.TestCase):
    def test_normal_and_cloud_overflow_states(self):
        limits = RawOverflowLimits(warning_ratio=0.8, stop_ratio=0.9)
        self.assertEqual(classify(hot_used_bytes=70, hot_budget_bytes=100, queue_batches=0, spool_bytes=0, cloud_enabled=True, limits=limits)[0], "normal")
        self.assertEqual(classify(hot_used_bytes=85, hot_budget_bytes=100, queue_batches=0, spool_bytes=0, cloud_enabled=True, limits=limits)[0], "cloud_overflow")

    def test_critical_wins_when_queue_or_spool_is_full(self):
        limits = RawOverflowLimits(max_queue_batches=2, max_spool_bytes=100)
        state, reasons = classify(hot_used_bytes=10, hot_budget_bytes=100, queue_batches=2, spool_bytes=0, cloud_enabled=True, limits=limits)
        self.assertEqual(state, "critical")
        self.assertIn("archive_queue_full", reasons)
        state, reasons = classify(hot_used_bytes=10, hot_budget_bytes=100, queue_batches=0, spool_bytes=100, cloud_enabled=True, limits=limits)
        self.assertEqual(state, "critical")
        self.assertIn("archive_spool_full", reasons)

    def test_missing_cloud_does_not_claim_cloud_overflow(self):
        state, reasons = classify(hot_used_bytes=85, hot_budget_bytes=100, queue_batches=0, spool_bytes=0, cloud_enabled=False)
        self.assertEqual(state, "normal")
        self.assertIn("hot_database_warning_watermark", reasons)

    def test_batch_size_is_hard_bounded(self):
        limits = RawOverflowLimits(max_batch_bytes=10)
        self.assertTrue(batch_allowed(batch_bytes=10, limits=limits))
        self.assertFalse(batch_allowed(batch_bytes=0, limits=limits))
        self.assertFalse(batch_allowed(batch_bytes=11, limits=limits))


if __name__ == "__main__":
    unittest.main()
