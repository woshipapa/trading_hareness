"""The declared port to the video harness, and the calendar that replaced it."""
from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import teacher_harness as harness  # noqa: E402


def _forget_harness_modules() -> None:
    for module in harness.CONTRACT:
        sys.modules.pop(module, None)


class HarnessContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.fake = Path(self._directory.name)
        for module, names in harness.CONTRACT.items():
            body = "".join(f"def {name}(*args, **kwargs):\n    return {module!r}\n" for name in names)
            (self.fake / f"{module}.py").write_text(body, encoding="utf-8")
        _forget_harness_modules()
        self._path = list(sys.path)

    def tearDown(self) -> None:
        _forget_harness_modules()
        sys.path[:] = self._path
        self._directory.cleanup()

    def test_a_complete_checkout_has_no_problems_and_loads_the_declared_names(self) -> None:
        self.assertEqual(harness.contract_problems(self.fake), [])
        self.assertEqual(harness.load("teacher_board", self.fake).dropped_bound_stocks(), "teacher_board")
        self.assertEqual(sys.path[0], str(self.fake))

    def test_a_renamed_function_is_reported_by_module_and_name(self) -> None:
        (self.fake / "teacher_owner_overlap.py").write_text("def compare_v2():\n    pass\n", encoding="utf-8")
        problems = harness.contract_problems(self.fake)
        self.assertEqual(len(problems), 1)
        self.assertIn("teacher_owner_overlap no longer provides compare", problems[0])

    def test_a_missing_checkout_or_an_undeclared_module_is_an_error(self) -> None:
        with self.assertRaises(harness.HarnessUnavailable):
            harness.load("owner_universe", self.fake / "absent")
        with self.assertRaises(KeyError):
            harness.load("teacher_strategy", self.fake)

    @unittest.skipUnless(harness.HARNESS_DIR.is_dir(), "no video harness checkout here")
    def test_the_real_checkout_meets_the_contract(self) -> None:
        _forget_harness_modules()
        self.assertEqual(harness.contract_problems(), [])


class CalendarTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("exchange_calendars"), "needs exchange_calendars")
    def test_holidays_are_not_sessions(self) -> None:
        self.assertFalse(harness.is_trading_day(dt.date(2026, 9, 25)))   # 中秋
        self.assertFalse(harness.is_trading_day(dt.date(2026, 10, 1)))   # 国庆
        self.assertTrue(harness.is_trading_day(dt.date(2026, 10, 9)))

    def test_without_the_calendar_weekdays_count(self) -> None:
        with patch.dict(sys.modules, {"exchange_calendars": None}):
            self.assertTrue(harness.is_trading_day(dt.date(2026, 9, 25)))
            self.assertFalse(harness.is_trading_day(dt.date(2026, 10, 10)))


if __name__ == "__main__":
    unittest.main()
