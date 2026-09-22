"""The daily teacher-review operator commands: context, check and pool views."""

from __future__ import annotations

import copy
import json
import unittest
from datetime import date
from pathlib import Path

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
