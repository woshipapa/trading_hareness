"""同一场复盘有两份稿子时挑哪一份。

2026-09-29 同一个视频被处理了两遍：自动入库投了一次（19:46），人又手工投了一次
（21:33）。两个任务的 ``review_date`` 都是那天，而原来只按复盘日排序、调用方取
``[-1]`` —— 最后用哪份**由 `iterdir()` 的顺序决定**。那天碰巧挑中了干净的一份
（159 个数字、2 个未核对、0 代码警告）；另一份有 14 条 unbound 代码警告，
雪龙集团、襄阳轴承、机器人都写成了"候选X（代码）"。这种事不能靠运气。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import tempfile
import unittest


def _cycle():
    spec = importlib.util.spec_from_file_location(
        "teacher_cycle", pathlib.Path(__file__).resolve().with_name("teacher_cycle.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class JobChoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cycle = _cycle()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)

    def _job(self, name: str, review: str, *, generated: str, problems: int = 0,
             warnings: int = 0, unverified: int = 0) -> pathlib.Path:
        job = self.root / name
        job.mkdir()
        strategy = f"teacher_strategy_{review.replace('-', '')}.md"
        (job / strategy).write_text("draft", encoding="utf-8")
        (job / "teacher_review_handoff.json").write_text(json.dumps({
            "review_date": review, "status": "completed", "generated_at": generated,
            "canonical_strategy_file": strategy,
            "number_check": {"code_problems": problems, "code_warnings": warnings,
                             "unverified_count": unverified},
        }, ensure_ascii=False), encoding="utf-8")
        return job

    def _chosen(self) -> str:
        return self.cycle.eligible_jobs(self.root)[-1][0].name

    def test_the_cleaner_draft_wins_even_though_it_came_first(self) -> None:
        """质量优先于时间：早生成但干净的那份应该赢。"""
        self._job("dirty", "2026-09-29", generated="2026-09-29T23:56:19+08:00", warnings=14, unverified=7)
        self._job("clean", "2026-09-29", generated="2026-09-29T23:52:48+08:00", warnings=0, unverified=2)
        self.assertEqual(self._chosen(), "clean")

    def test_the_九二九_shape_picks_the_job_we_actually_used(self) -> None:
        self._job("48d37236edd74bb1", "2026-09-29", generated="2026-09-29T23:52:48+08:00",
                  warnings=14, unverified=7)
        self._job("a06b70110f264807", "2026-09-29", generated="2026-09-29T23:56:19+08:00",
                  warnings=0, unverified=2)
        self.assertEqual(self._chosen(), "a06b70110f264807")

    def test_a_blocking_code_problem_outranks_every_other_signal(self) -> None:
        self._job("has_problem", "2026-09-29", generated="2026-09-29T23:59:00+08:00", problems=1)
        self._job("no_problem", "2026-09-29", generated="2026-09-29T20:00:00+08:00",
                  warnings=99, unverified=99)
        self.assertEqual(self._chosen(), "no_problem")

    def test_identical_quality_falls_back_to_the_later_draft(self) -> None:
        self._job("earlier", "2026-09-29", generated="2026-09-29T20:00:00+08:00")
        self._job("later", "2026-09-29", generated="2026-09-29T23:00:00+08:00")
        self.assertEqual(self._chosen(), "later")

    def test_a_later_review_date_still_wins_over_a_cleaner_older_one(self) -> None:
        """跨天时复盘日仍然是第一顺位，质量只在同一天内比。"""
        self._job("old_clean", "2026-09-28", generated="2026-09-28T23:00:00+08:00")
        self._job("new_dirty", "2026-09-29", generated="2026-09-29T23:00:00+08:00",
                  warnings=14, unverified=7)
        self.assertEqual(self._chosen(), "new_dirty")


if __name__ == "__main__":
    unittest.main()
