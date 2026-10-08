"""自动导入的时间窗。

手工导入时人自己看得见钟点；自动化每 20 分钟一轮，稿子出晚了就会在盘中把"今天"的
计划导进池子 —— 一个已经过了半场的交易日，计划不该再自动入池。
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import os
import unittest
from pathlib import Path

CN = dt.timezone(dt.timedelta(hours=8))
# The exchange holiday calendar lives in the video harness repository; without
# it teacher_cycle falls back to weekdays by design, so a holiday cannot be told
# apart from a session (CI does not check that repository out).
HARNESS_CALENDAR = Path(os.environ.get(
    "VIDEO_HARNESS_DIR", "/Users/papa/codebase/video_understanding_harness")) / "teacher_strategy.py"


def _cycle():
    spec = importlib.util.spec_from_file_location(
        "teacher_cycle", Path(__file__).resolve().with_name("teacher_cycle.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ImportWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cycle = _cycle()

    def _at(self, target: str | None, stamp: str) -> tuple[bool, str]:
        return self.cycle.import_window(target, dt.datetime.fromisoformat(stamp).replace(tzinfo=CN))

    def test_a_future_session_imports_at_any_hour(self) -> None:
        for stamp in ("2026-09-25 07:00", "2026-09-25 11:30", "2026-09-25 23:00"):
            ok, _ = self._at("2026-09-28", stamp)
            self.assertTrue(ok, stamp)

    def test_today_before_the_open_still_imports(self) -> None:
        ok, _ = self._at("2026-09-28", "2026-09-28 07:30")
        self.assertTrue(ok)

    def test_today_during_the_session_is_refused(self) -> None:
        ok, reason = self._at("2026-09-28", "2026-09-28 10:30")
        self.assertFalse(ok)
        self.assertIn("盘中不自动入池", reason)

    def test_today_after_the_close_is_refused(self) -> None:
        ok, reason = self._at("2026-09-28", "2026-09-28 16:20")
        self.assertFalse(ok)
        self.assertIn("这一场已经过去", reason)

    @unittest.skipUnless(HARNESS_CALENDAR.exists(), "the holiday calendar lives in the video harness repository")
    def test_a_closed_day_has_no_session_to_be_inside_of(self) -> None:
        # 2026-09-25 中秋休市：目标是今天、钟点在盘中区间，但没有盘
        ok, _ = self._at("2026-09-25", "2026-09-25 10:30")
        self.assertTrue(ok)

    def test_a_past_session_is_refused(self) -> None:
        """目标交易日已经过去：结算都做完了，再入池只会污染观察池。"""
        ok, reason = self._at("2026-09-24", "2026-09-25 18:00")
        self.assertFalse(ok)
        self.assertIn("已经过去", reason)

    def test_a_missing_target_session_is_refused(self) -> None:
        ok, reason = self._at(None, "2026-09-25 18:00")
        self.assertFalse(ok)
        self.assertIn("target_session", reason)


class ImportLedgerTests(unittest.TestCase):
    """导入记录的判定。自动化之后，"退出码 0 就算导过了"会把一次静默失败变成永远跳过。"""

    def setUp(self) -> None:
        self.cycle = _cycle()

    def test_a_real_import_is_not_repeated(self) -> None:
        for entry in ({"json": {"import": {"status": "imported"}}, "returncode": 0},
                      {"json": {"status": "imported"}, "returncode": 0}):
            verdict, note = self.cycle.import_state(entry)
            self.assertEqual(verdict, "done")
            self.assertIn("不重复导入", note)

    def test_a_failed_import_can_be_retried(self) -> None:
        for entry in ({"json": {"import": {"status": "failed"}}, "returncode": 1},
                      {"returncode": 1}, None, "not a dict"):
            self.assertEqual(self.cycle.import_state(entry)[0], "retry")

    def test_a_zero_exit_without_a_status_is_flagged_not_assumed(self) -> None:
        verdict, note = self.cycle.import_state({"returncode": 0})
        self.assertEqual(verdict, "ambiguous")
        self.assertIn("无法确认", note)


if __name__ == "__main__":
    unittest.main()
