"""收盘后才跑的那几步，靠"现在是不是盘中"把关。

以前判断用 ``now.weekday() < 5``：休市的工作日（中秋、国庆）也算交易日，
于是 09:15–15:00 之间 sweep 一律被拒，每 20 分钟一轮的自动化空转半天。
"""
from __future__ import annotations

import importlib.util
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

CN = timezone(timedelta(hours=8))
DRIVER = Path(__file__).resolve().with_name("teacher_review_daily.py")
# The exchange holiday calendar lives in the video harness repository; without
# it in_session_now falls back to weekdays by design (CI has no harness checkout).
HARNESS_CALENDAR = Path(os.environ.get(
    "VIDEO_HARNESS_DIR", "/Users/papa/codebase/video_understanding_harness")) / "teacher_strategy.py"


def _driver():
    spec = importlib.util.spec_from_file_location("teacher_review_daily", DRIVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SessionGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.driver = _driver()

    def _at(self, stamp: str) -> bool:
        return self.driver.in_session_now(datetime.fromisoformat(stamp).replace(tzinfo=CN))

    @unittest.skipUnless(HARNESS_CALENDAR.exists(), "the holiday calendar lives in the video harness repository")
    def test_a_closed_weekday_is_not_a_session(self) -> None:
        # 2026-09-25 是中秋休市的周五 —— 这一条是修复的理由
        self.assertFalse(self._at("2026-09-25 10:30"))
        self.assertFalse(self._at("2026-10-01 10:30"))   # 国庆

    def test_a_real_session_is_recognised(self) -> None:
        self.assertTrue(self._at("2026-09-24 10:30"))
        self.assertTrue(self._at("2026-09-28 10:30"))    # 节后第一个交易日
        self.assertTrue(self._at("2026-09-24 09:15"))    # 边界：含左端
        self.assertFalse(self._at("2026-09-24 15:00"))   # 边界：不含右端

    def test_outside_session_hours_is_never_a_session(self) -> None:
        for stamp in ("2026-09-24 08:00", "2026-09-24 16:30", "2026-09-24 23:59"):
            self.assertFalse(self._at(stamp))

    def test_weekends_are_not_sessions(self) -> None:
        self.assertFalse(self._at("2026-09-26 10:30"))
        self.assertFalse(self._at("2026-09-27 10:30"))


if __name__ == "__main__":
    unittest.main()
