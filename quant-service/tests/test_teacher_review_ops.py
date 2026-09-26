"""The daily teacher-review operator commands: context, check and pool views."""

from __future__ import annotations

import copy
import json
import unittest
from datetime import date
from pathlib import Path

from app import teacher_review_ops as ops
from app.teacher_review_ops import build_context, check_report, context_markdown, pack_digest, pool_view

FIXTURE = Path(__file__).parent / "fixtures" / "teacher_review_pack_20260921.json"


def row(symbol, state, *, enabled=True, review_date="2026-09-21", playbook="platform_breakout", reason=None):
    return {"symbol": symbol, "enabled": enabled, "metadata": {"teacher_review": {
        "name": symbol[:6], "session_date": "2026-09-23", "status": "observe" if state == "observe" else "active",
        "playbook": playbook, "pack_id": "a8f029c43ac97083", "review_date": review_date,
        "params": {"prior_high": 10.0}, "lifecycle": {"state": state, "reason": reason}}}}


class PoolAndContextTests(unittest.TestCase):
    def test_the_pool_lists_enabled_plans_new_first(self):
        pool = pool_view([row("B.SZ", "observe"), row("A.SZ", "promoted"), row("C.SZ", "new"),
                          row("D.SZ", "observe", enabled=False)])
        self.assertEqual([item["symbol"] for item in pool], ["C.SZ", "A.SZ", "B.SZ"])
        self.assertEqual(pool[1]["key_level"], 10.0)

    def test_context_carries_settlement_lifecycle_and_close(self):
        settlement = {"trade_date": "2026-09-22", "roll": {"next_session": "2026-09-23", "lifecycle": [
            {"code": "605258", "name": "协和电子", "from": "new", "state": "promoted", "reason": "收盘站上 39.6"}]},
            "packs": [{"pack_id": "a8f029c43ac97083", "review_date": "2026-09-21", "session_index": 1,
                       "forecasts": [{"id": "F1", "text": "前六至少成两个", "hit": True, "value": ["001216"]}],
                       "stocks": [{"code": "002815", "name": "崇达技术", "kind": "trend",
                                   "entry": {"at": "2026-09-22T09:41", "price": 25.18}, "entry_to_close_pct": 0.83},
                                  {"code": "603230", "name": "内蒙新华", "kind": "record",
                                   "bar": {"pct": 10.0}, "closed_at_limit": True}]}]}
        context = build_context(date(2026, 9, 22), settlement=settlement, rows=[row("605258.SH", "promoted")],
                                packs=[{"pack_id": "a8f029c43ac97083", "review_date": "2026-09-21",
                                        "available_at": "x", "stocks": 47}],
                                bars={"605258.SH": {"close": 42.16, "pct": 10.0, "high": 42.16}},
                                receipts={"teacher_review_roll": "completed"})
        self.assertTrue(context["settled"])
        self.assertEqual(context["packs"][0]["triggered"][0]["name"], "崇达技术")
        text = context_markdown(context)
        self.assertIn("| 协和电子（605258） | 新计划 | 晋级延续 | 收盘站上 39.6 |", text)
        self.assertIn("内蒙新华+10.0%封板", text)
        self.assertIn("605258.SH）+10.00%", text)

    def test_an_unsettled_session_says_so(self):
        context = build_context(date(2026, 9, 22), settlement=None, rows=[], packs=[], bars={}, receipts={})
        self.assertIn("当日结算尚未生成", context_markdown(context))


class CheckReportTests(unittest.TestCase):
    def setUp(self):
        self.pack = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.instruments = {f"{s['code']}.{'SH' if s['code'].startswith('6') else 'SZ'}": s["name"]
                            for s in self.pack["stocks"]}

    def test_a_clean_pack_passes_and_lists_what_it_overrides(self):
        pool = pool_view([row("002285.SZ", "promoted", review_date="2026-09-20"),   # re-mentioned: replaced
                          row("600127.SH", "observe", review_date="2026-09-20"),    # re-mentioned as rejected: retired
                          row("300001.SZ", "observe", review_date="2026-09-20")])   # not mentioned: carried on
        report = check_report(self.pack, instruments=self.instruments, pool=pool,
                              dry_run={"status": "dry_run", "planned": ["002285.SZ"]}, duplicate=False)
        self.assertTrue(report["ok"], report["problems"])
        effects = {item["symbol"]: item["effect"] for item in report["overrides"]}
        self.assertEqual(effects, {"002285.SZ": "replace", "600127.SH": "retire"})
        self.assertEqual([item["symbol"] for item in report["carried_unmentioned"]], ["300001.SZ"])
        own = pool_view([row("002285.SZ", "new", review_date=self.pack["review_date"])])
        own[0]["pack_id"] = self.pack["pack_id"]
        rerun = check_report(self.pack, instruments=self.instruments, pool=own, dry_run=None, duplicate=True)
        self.assertEqual(rerun["overrides"], [])          # its own applied plans are not overrides

    def test_unknown_codes_duplicates_and_structure_block_the_import(self):
        broken = copy.deepcopy(self.pack)
        broken["stocks"][0]["playbook"] = "made_up"
        instruments = dict(self.instruments)
        instruments.pop("002285.SZ")
        report = check_report(broken, instruments=instruments, pool=[], dry_run=None, duplicate=True)
        self.assertFalse(report["ok"])
        joined = " ".join(report["problems"])
        self.assertIn("unknown playbook", joined)
        self.assertIn("002285", joined)
        self.assertIn("already imported", joined)

    def test_the_pack_id_convention_is_the_content_digest(self):
        pack = {"schema": "x", "stocks": []}
        pack["pack_id"] = pack_digest(pack)
        self.assertEqual(pack_digest(pack), pack["pack_id"])
        report = check_report({**self.pack, "pack_id": "0000000000000000"}, instruments=self.instruments,
                              pool=[], dry_run={"status": "dry_run"}, duplicate=False)
        self.assertTrue(any("content digest" in item for item in report["warnings"]))


if __name__ == "__main__":
    unittest.main()


class OutcomeDigestTests(unittest.TestCase):
    """Yesterday's lesson has to reach tomorrow's pack, not sit in an archive."""

    def outcome(self):
        return {
            "trade_date": "2026-09-22", "benchmark_session_pct": 0.8,
            "counts": {"hit": 7, "missed": 5, "unbuyable": 2},
            "stocks": [
                {"code": "603316", "name": "诚邦股份", "playbook": "trend_pullback_restart", "outcome": "missed",
                 "opportunity_pct": 10.02, "blocked_by": "再起三条件（全天 100% 未满足）"},
                {"code": "000504", "name": "南华生物", "outcome": "unbuyable", "entry": {"at": "09:33"}},
                {"code": "000910", "name": "大亚圣象", "outcome": "avoid_missed", "close_pct": 10.1},
                {"code": "300741", "name": "华宝股份", "outcome": "hit", "measures": {}},
            ],
            "learning": {"session_count": 3,
                         "playbooks": [{"playbook": "platform_breakout", "total": 15, "hit": 4,
                                        "triggered_faded": 1, "missed": 3, "hit_rate_pct": 26.7,
                                        "net_mean_pct": 1.8}],
                         "suggestions": [{"playbook": "platform_breakout", "gate": "量比≥1.5（带量）",
                                          "missed_cases": 3, "mean_pct": 7.5,
                                          "note": "platform_breakout 漏掉的大涨里，「量比≥1.5（带量）」卡了 3 次"}],
                         "unpushed": [{"name": "奥士康", "scans": 79}], "data_gaps": []},
        }

    def test_the_digest_keeps_what_changes_a_decision(self):
        digest = ops.outcome_digest(self.outcome())
        self.assertEqual([item["name"] for item in digest["missed"]], ["诚邦股份"])
        self.assertEqual([item["name"] for item in digest["unbuyable"]], ["南华生物"])
        self.assertEqual([item["name"] for item in digest["rejected_but_ran"]], ["大亚圣象"])
        self.assertEqual(digest["session_count"], 3)

    def test_an_absent_review_leaves_the_context_unchanged(self):
        self.assertEqual(ops.outcome_digest(None), {})

    def test_the_page_tells_the_builder_what_to_do_with_it(self):
        context = ops.build_context(date(2026, 9, 22), settlement=None, rows=[], packs=[], bars={},
                                    receipts={}, outcome=self.outcome())
        page = ops.context_markdown(context)
        self.assertIn("没触发却大涨的（条件可能太紧）", page)
        self.assertIn("诚邦股份", page)
        self.assertIn("封板价才触发", page)
        self.assertIn("老师否定却大涨的", page)
        self.assertIn("复核建议", page)
        self.assertIn("重放满足条件却没推送", page)
