from __future__ import annotations

import unittest

from app.scheduler_error_detail import scheduler_error_detail


class SchedulerErrorDetailTests(unittest.TestCase):
    def test_empty_exception_string_keeps_type_and_repr(self):
        detail = scheduler_error_detail(TimeoutError())
        self.assertEqual(detail, "TimeoutError: TimeoutError()")

    def test_detail_is_bounded(self):
        detail = scheduler_error_detail(RuntimeError("x" * 100), limit=20)
        self.assertEqual(len(detail), 20)
        self.assertTrue(detail.startswith("RuntimeError:"))


if __name__ == "__main__":
    unittest.main()
