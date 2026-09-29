"""闭环报告写的是**文件里现在的事实**，不是这一轮 state 里的旧值。

2026-09-28 那晚同时出了两个假象，因为报告信的是 state：

* ``pack`` 那一步第二轮就被 ``done()`` 跳过了，于是报告一直印第一轮的
  pack_id 和"个股 21 只"，而包在那之后被改过（补了 3 个代码、去掉一条板块条目）；
* 人补完代码手工导入之后，``check`` 只剩一条 ``already imported`` —— 那是成功，
  却和"代码不是六位"一样被算成 problems 非空，于是每一轮都写
  "check 未通过 → 未导入（等人确认）"，看板上像还没入池。
"""
from __future__ import annotations

import datetime as dt
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


class ReportFactsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cycle = _cycle()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.job = pathlib.Path(self.tmp.name)
        (self.job / "teacher_pack_20260928.json").write_text(json.dumps({
            "pack_id": "15170ca3cdf03e22",
            "stocks": [{"code": f"00{index:04d}"} for index in range(20)],
            "forecasts": [{"id": "f1"}], "method_notes": [{}] * 18}, ensure_ascii=False), encoding="utf-8")
        (self.job / "pack_check_20260928.json").write_text(json.dumps({
            "ok": False, "problems": ["pack 15170ca3cdf03e22 is already imported"], "warnings": [],
            "counts": {"watched": 16, "record_only": 4}}, ensure_ascii=False), encoding="utf-8")
        (self.job / "import_report_20260928.json").write_text(json.dumps({
            "import": {"status": "imported", "session_date": "2026-09-29",
                       "planned": ["603396.SH"] * 15, "plan_failures": [{}, {}]}}), encoding="utf-8")

    def _report(self, state: dict) -> str:
        path = self.cycle.report(self.job, "2026-09-28", state)
        return path.read_text(encoding="utf-8")

    def test_the_pack_facts_come_from_the_file_not_the_stale_state(self) -> None:
        stale = {"pack": {"status": "exists", "pack_file": "teacher_pack_20260928.json",
                          "pack_id": "bf508789ba77917e", "stocks": 21}}
        text = self._report(stale)
        self.assertIn("15170ca3cdf03e22", text)
        self.assertIn("个股 20 只", text)
        self.assertNotIn("bf508789ba77917e", text)
        self.assertNotIn("个股 21 只", text)

    def test_already_imported_reads_as_imported_not_as_a_hold(self) -> None:
        text = self._report({"check": {"returncode": 1}})
        self.assertIn("check：ok（", text)
        self.assertIn("已在池中", text)
        self.assertIn("阻断 0", text)
        self.assertIn("**导入**：imported", text)
        self.assertIn("planned 15", text)
        self.assertNotIn("未导入（等人确认）", text)

    def test_the_import_step_line_reflects_the_pool_when_a_human_imported(self) -> None:
        text = self._report({"check": {"returncode": 1}})
        self.assertIn("- import：imported（按 import_report 文件）", text)

    def test_a_real_problem_still_blocks(self) -> None:
        (self.job / "pack_check_20260928.json").write_text(json.dumps({
            "ok": False, "problems": ["None 机器人: code must be six digits",
                                      "pack 15170ca3cdf03e22 is already imported"],
            "warnings": [], "counts": {}}, ensure_ascii=False), encoding="utf-8")
        text = self._report({"check": {"returncode": 1}})
        self.assertIn("check：失败（exit 1）", text)
        self.assertIn("阻断 1", text)
        self.assertIn("- 阻断：None 机器人", text)
        self.assertIn("- 已导入：pack", text)


if __name__ == "__main__":
    unittest.main()
