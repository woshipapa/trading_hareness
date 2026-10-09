"""Tests for scripts/b300_collab_dashboard.py (parsers and rendering; no network, no git)."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("b300_collab_dashboard", HERE / "b300_collab_dashboard.py")
dash = importlib.util.module_from_spec(spec)
sys.modules["b300_collab_dashboard"] = dash
spec.loader.exec_module(dash)

QUEUE = """# 任务队列

最近更新：2026-10-09，第十五份审查（频率轴）。

## 第一阶段

| 编号 | 任务 | 负责方 | 依赖 | 依据 | 验收 | 状态 |
|---|---|---|---|---|---|---|
| Q03 | 锁频选档：短 campaign、压力测试 | 执行方 | — | 手册 4.1 | L 写进 STATUS | 运行中（960 不稳） |
| Q05 | 审查方：把 L 写进 `platforms.py` | 审查方 | Q03 | 第五份审查 | 提交 | 未开始 |
| Q09 | 带 `a \\| b` 的任务 | 执行方 | Q03 | 手册 8.1 | — | 需返工 |

## 已关闭

| 编号 | 任务 | 负责方 | 依赖 | 依据 | 验收 | 状态 |
|---|---|---|---|---|---|---|
| Q01 | 门禁重跑 | 执行方 | — | 手册 6 | 全部 PASS | 已结案（d2672d1） |
"""

FIXES = """| 日期（UTC） | 提交 | 条目 | 改了什么 | 需重跑的实验 | 状态 |
|---|---|---|---|---|---|
| 2026-10-08 | 615d683 | 2.4 | **合入旧分支的改动。** 细节 | 不需重跑 | 完成 |
| 2026-10-09 | c86131f | 1.42（提案 65c9038） | **锁频下计数器只记录。** 细节 | 不用重跑 | 完成 |
| 2026-10-09 | （本批提交） | 1.49（提案 `proposals/20261009_run_dir_name_collision.md`） | **目录名加卡号。** | 不用重跑 | 完成 |
"""


class ParserTests(unittest.TestCase):
    def test_queue_rows_status_classes_and_escaped_pipes(self):
        tasks = dash.parse_queue(QUEUE)
        self.assertEqual([t["id"] for t in tasks], ["Q03", "Q05", "Q09", "Q01"])
        by = {t["id"]: t for t in tasks}
        self.assertEqual(by["Q03"]["status_class"], "running")
        self.assertEqual(by["Q09"]["status_class"], "blocked")
        self.assertEqual(by["Q01"]["status_class"], "closed")
        self.assertEqual(by["Q01"]["section"], "已关闭")
        self.assertEqual(by["Q09"]["task"], "带 a | b 的任务")
        self.assertEqual(by["Q05"]["task"], "审查方：把 L 写进 platforms.py")
        # readiness: Q05 waits for Q03 (running); nothing in this table is ready
        self.assertEqual((by["Q05"]["status_class"], by["Q05"]["waiting_on"]), ("waiting", ["Q03"]))

    def test_a_task_whose_dependencies_are_closed_is_ready(self):
        text = QUEUE.replace("| Q05 | 审查方：把 L 写进 `platforms.py` | 审查方 | Q03 |",
                             "| Q05 | 审查方：把 L 写进 `platforms.py` | 审查方 | Q01 |")
        by = {t["id"]: t for t in dash.parse_queue(text)}
        self.assertEqual((by["Q05"]["status_class"], by["Q05"]["waiting_on"]), ("ready", []))

    def test_fixes_newest_appended_first_and_bold_title(self):
        fixes = dash.parse_fixes(FIXES)
        self.assertEqual([f["num"] for f in fixes], ["1.49", "1.42", "2.4"])
        self.assertEqual(fixes[0]["title"], "目录名加卡号。")
        self.assertEqual(fixes[1]["commit"], "c86131f")

    def test_chinese_ordinals(self):
        for text, n in (("八", 8), ("十", 10), ("十五", 15), ("二十", 20), ("二十三", 23)):
            self.assertEqual(dash.cn_number(text), n)

    def test_reviews_responses_and_proposals(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "review").mkdir()
            (t / "response").mkdir()
            (t / "proposals").mkdir()
            (t / "review" / "20261009_a.md").write_text("# 审查 2026-10-09（第十四份）：930 计时不稳\n", encoding="utf-8")
            (t / "review" / "20261009_b.md").write_text("# 审查 2026-10-09（第十五份）：频率轴\n", encoding="utf-8")
            (t / "response" / "20261009_a.response.md").write_text("ok", encoding="utf-8")
            (t / "proposals" / "20261008_old.md").write_text("# 建议：过滤 NaN\n", encoding="utf-8")
            (t / "proposals" / "20261009_new.md").write_text("# 提案：目录撞名\n", encoding="utf-8")
            (t / "proposals" / "20261009_open.md").write_text("# 提案：还没答复\n", encoding="utf-8")
            reviews = dash.parse_reviews(t / "review", t / "response")
            self.assertEqual([(r["ordinal"], r["responded"]) for r in reviews], [(15, False), (14, True)])
            self.assertEqual(reviews[0]["title"], "频率轴")
            answers = "采纳提案 b9a35ca；proposals/20261009_new.md"
            props = {p["file"]: p for p in dash.parse_proposals(t / "proposals", answers,
                                                                 {"20261008_old.md": "b9a35ca1"})}
            self.assertTrue(props["20261008_old.md"]["answered"])        # cited by commit sha
            self.assertTrue(props["20261009_new.md"]["answered"])        # cited by file name
            self.assertFalse(props["20261009_open.md"]["answered"])
            self.assertEqual(props["20261008_old.md"]["title"], "过滤 NaN")

    def test_heartbeat_takes_the_newest_block(self):
        hb = dash.parse_heartbeat("# 心跳\n\n## 2026-10-09 08:47Z\n\n- **在跑**：GPU4 960\n- 下一步\n\n## 2026-10-09 07:39Z\n\n- 旧\n")
        self.assertEqual(hb["time"], "2026-10-09 08:47Z")
        self.assertEqual(hb["items"], ["在跑：GPU4 960", "下一步"])

    def test_commit_sides_and_kinds(self):
        raw = "\n".join(["a" * 40 + "\x1fb300-exec-agent\x1f2026-10-09T00:45:00-07:00\x1frun: 960 GPU2 decide",
                         "b" * 40 + "\x1fyp\x1f2026-10-09T01:00:00-07:00\x1ffix(b300-watch): x"])
        cs = dash.parse_commits(raw)
        self.assertEqual([(c["side"], c["kind"]) for c in cs], [("executor", "run"), ("reviewer", "fix")])


class EventTests(unittest.TestCase):
    PREV = {
        "queue": [{"id": "Q03", "owner": "执行方", "task": "锁频选档", "status_class": "running"},
                  {"id": "Q09", "owner": "执行方", "task": "追加团队", "status_class": "waiting"}],
        "reviews": [{"file": "15.md", "ordinal": 15, "title": "频率轴", "responded": False}],
        "proposals": [{"file": "old.md", "title": "目录撞名", "answered": False}],
        "fixes": [{"num": "1.49", "title": "目录名"}],
        "runs": [{"name": "r1", "kind": "选档压力测试", "lock": "960", "gpu": "2", "audit": "pass"}],
        "lock_choice": {"file": "lock_choice_960.json", "chosen": None}, "lock_history": [],
        "h100": {"state": "live", "timings": [{"run": "t1200_p1", "lock": 1200}], "screens": [{"lock": 1200, "passing": 24, "cells": 25}],
                 "log": ["[08:00] lock 1620: power screen"]},
    }

    def cur(self):
        import copy
        c = copy.deepcopy(self.PREV)
        c["queue"][0]["status_class"] = "closed"
        c["queue"][1]["status_class"] = "blocked"
        c["queue"].append({"id": "Q40", "owner": "审查方", "task": "新任务", "status_class": "waiting"})
        c["reviews"][0]["responded"] = True
        c["reviews"].append({"file": "16.md", "ordinal": 16, "title": "新审查", "responded": False})
        c["proposals"][0]["answered"] = True
        c["proposals"].append({"file": "new.md", "title": "新提案", "answered": False})
        c["fixes"].insert(0, {"num": "1.50", "title": "新修复"})
        c["runs"] = [{"name": "r3", "kind": "选档短 campaign", "lock": "975", "gpu": "4", "audit": "fail"},
                     {"name": "r2", "kind": "选档短 campaign", "lock": "990", "gpu": "2", "audit": "pass"}] + c["runs"]
        c["lock_choice"] = {"file": "lock_choice_975.json", "chosen": 975}
        c["lock_history"] = [{"file": "lock_choice_960.json", "chosen": None}]
        c["h100"]["timings"].append({"run": "t1020_p3", "lock": 1020, "p90": 0.012, "audit": "pass"})
        c["h100"]["screens"] += [{"lock": 1020, "passing": 24, "cells": 25}, {"lock": 1830, "passing": 0, "cells": 25}]
        c["h100"]["log"].append("[09:00] LADDER STOPPED: timing at 1410 failed: correctness")
        return c

    def test_every_kind_of_change_and_its_flags(self):
        ev = {e["type"] + ":" + e["text"][:12]: e for e in dash.diff_snapshots(self.PREV, self.cur())}
        kinds = [k.split(":")[0] for k in ev]
        for kind in ("task_status", "task_new", "review_new", "response_new", "proposal_new", "proposal_answered",
                     "fix_new", "run_new", "lock_chosen", "h100_timing", "h100_screen", "h100_stopped"):
            self.assertIn(kind, kinds, kind)
        flags = {e["type"]: (e["attention"], e["trigger"]) for e in dash.diff_snapshots(self.PREV, self.cur())
                 if e["type"] in ("response_new", "proposal_new", "lock_chosen", "h100_stopped", "fix_new", "review_new")}
        self.assertEqual(flags["response_new"], (True, True))
        self.assertEqual(flags["proposal_new"], (True, True))
        self.assertEqual(flags["lock_chosen"], (True, True))
        self.assertEqual(flags["h100_stopped"], (True, True))
        self.assertEqual(flags["fix_new"], (False, False))          # our own pushes are recorded, not sent alone
        self.assertEqual(flags["review_new"], (False, False))
        runs = [e for e in dash.diff_snapshots(self.PREV, self.cur()) if e["type"] == "run_new"]
        self.assertEqual([(e["attention"], "fail" in e["text"]) for e in runs], [(False, False), (True, True)])
        blocked = [e for e in dash.diff_snapshots(self.PREV, self.cur()) if e["type"] == "task_status" and "Q09" in e["text"]]
        self.assertEqual((blocked[0]["attention"], blocked[0]["trigger"]), (True, True))
        self.assertIn("等依赖 → 需返工/等修复", blocked[0]["text"])
        self.assertEqual(dash.diff_snapshots(self.PREV, self.PREV), [])  # nothing changed, no events

    def test_short_cuts_at_a_clause_or_marks_the_cut(self):
        self.assertEqual(dash.short("990 MHz 不过属实；990 短 campaign 的校准空洞（修复 1.44）；930 不稳", 45),
                         "990 MHz 不过属实；990 短 campaign 的校准空洞（修复 1.44）")
        self.assertEqual(dash.short("abcdefghij", 5), "abcd…")
        self.assertEqual(dash.short("abc", 5), "abc")

    def test_events_are_appended_unsent_with_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            dash.record_events([{"type": "x", "side": "h100", "text": "a", "attention": False, "trigger": True}],
                               "2026-10-09T09:00:00Z", "abcdef1234", path)
            dash.record_events([{"type": "y", "side": "executor", "text": "b", "attention": True, "trigger": True}],
                               "2026-10-09T09:05:00Z", "abcdef1234", path)
            events = dash.load_events(path)
            self.assertEqual([e["id"] for e in events], ["20261009T090000Z-0", "20261009T090500Z-0"])
            self.assertEqual({e["sent"] for e in events}, {None})


class RenderTests(unittest.TestCase):
    def data(self, task_text="锁频选档"):
        q = dash.parse_queue(QUEUE.replace("锁频选档：短 campaign、压力测试", task_text))
        return {"generated_at": "2026-10-09T08:50:00Z", "tip": "1446995412345678", "branch_url": dash.BRANCH_URL,
                "queue": q, "queue_updated": "2026-10-09", "fixes": dash.parse_fixes(FIXES), "reviews": [],
                "proposals": [], "heartbeat": {"time": "", "items": []}, "commits": [],
                "lock_choice": {"file": "lock_choice_x.json", "chosen": None, "control": True, "why_none": [],
                                "locks": [{"lock": 960, "passes": False, "why": ["timing is not stable"]}]},
                "lock_history": [], "runs": [], "h100": {"state": "unreachable"}}

    def test_page_has_every_section(self):
        page = dash.render(self.data())
        for text in ("B300 协作进展", "执行方现在在做", "H100 频率轴", "锁频选档", "任务队列",
                     "提交时间线", "审查与回复", "审查方修复", "最近交付的运行目录", "Q03"):
            self.assertIn(text, page)

    def test_recent_changes_show_what_was_sent(self):
        d = self.data()
        d["events"] = [{"at": "2026-10-09T09:00:00Z", "side": "executor", "text": "执行方提案：<b>x</b>", "attention": True,
                        "trigger": True, "sent": None},
                       {"at": "2026-10-09T09:05:00Z", "side": "h100", "text": "H100 1020 MHz 完成", "attention": False,
                        "trigger": True, "sent": "sent 2026-10-09T09:06:00Z"},
                       {"at": "2026-10-09T09:05:00Z", "side": "reviewer", "text": "修复 1.50", "attention": False,
                        "trigger": False, "sent": None}]
        page = dash.render(d)
        self.assertIn("最近变化", page)
        for text in ("已发飞书", "待发", "只记录", "&lt;b&gt;x&lt;/b&gt;", 'class="attn"'):
            self.assertIn(text, page)

    def test_repository_text_is_escaped(self):
        page = dash.render(self.data(task_text="<script>alert(1)</script>"))
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)


class ServeRouteTests(unittest.TestCase):
    def test_index_server_serves_the_generated_page(self):
        spec2 = importlib.util.spec_from_file_location("serve_service_index", HERE / "serve_service_index.py")
        serve = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(serve)
        self.assertEqual(serve.GENERATED["/b300"][0].name, dash.OUT_HTML.name)
        self.assertEqual(serve.GENERATED["/b300.json"][0].name, dash.OUT_JSON.name)


if __name__ == "__main__":
    unittest.main()
