"""One page per session, assembled only from what each stage archived."""

from __future__ import annotations

import unittest
from datetime import date

from app.daily_research_digest import MIN_OBSERVATIONS, build, decisions, digest_markdown, digest_text


def teacher_report(**overrides):
    base = {
        "trade_date": "2026-09-22", "benchmark_session_pct": 0.8,
        "counts": {"hit": 7, "triggered_faded": 4, "unbuyable": 2, "missed": 5},
        "stocks": [
            {"name": "诚邦股份", "outcome": "missed", "opportunity_pct": 10.02,
             "blocked_by": "再起三条件（全天 100% 未满足）"},
            {"name": "会稽山", "outcome": "missed", "opportunity_pct": 8.89, "blocked_by": "回踩当日MA5"},
            {"name": "南华生物", "outcome": "unbuyable"},
            {"name": "华瓷股份", "outcome": "unbuyable"},
        ],
        "learning": {"session_count": 3, "playbooks": [
            {"playbook": "platform_breakout", "total": 15, "hit": 4, "triggered_faded": 1,
             "unbuyable": 2, "missed": 3, "hit_rate_pct": 26.7, "net_mean_pct": 1.8, "excess_mean_pct": 0.9}],
            "suggestions": [{"playbook": "platform_breakout", "gate": "量比≥1.5（带量）",
                             "missed_cases": 3, "mean_pct": 7.5}],
            "unpushed": [{"name": "奥士康", "scans": 79, "date": "2026-09-22"}]},
    }
    return {**base, **overrides}


def mode(name, **overrides):
    base = {"mode": name, "observations": 8, "alerted": 3, "avg_session_pct": 1.2,
            "avg_net_session_pct": 0.94, "avg_excess_pct": 0.4, "session_win_pct": 55.0,
            "avg_net_next_open_to_close_pct": -0.2}
    return {**base, **overrides}


def change(verdict="as_expected"):
    return {"change_id": "abc", "strategy": "teacher_review", "scope": "platform_breakout",
            "parameter": "vol_ratio_min", "from": 1.5, "to": 1.2, "applied_on": "2026-09-20",
            "measure": "missed_rate_pct", "direction": "down", "before": 60.0, "after": 70.0,
            "moved": 10.0, "verdict": verdict, "sessions_before": 2, "sessions_after": 3,
            "review_after_sessions": 3, "reason": "漏太多"}


class DecisionTests(unittest.TestCase):
    def test_the_biggest_miss_leads_with_what_blocked_it(self):
        lines = decisions(teacher_report(), [])
        self.assertIn("诚邦股份", lines[0])
        self.assertIn("再起三条件", lines[0])

    def test_unbuyable_entries_are_named_as_a_design_problem_not_a_threshold_one(self):
        line = next(line for line in decisions(teacher_report(), []) if "买不到" in line)
        self.assertIn("南华生物", line)
        self.assertIn("不是阈值问题", line)

    def test_a_silent_push_says_check_the_system_first(self):
        line = next(line for line in decisions(teacher_report(), []) if "没推送" in line)
        self.assertIn("奥士康", line)
        self.assertIn("别放宽条件", line)

    def test_a_change_that_went_the_wrong_way_asks_for_a_decision(self):
        line = next(line for line in decisions(teacher_report(), [change("against_expectation")])
                    if "与预期相反" in line)
        self.assertIn("vol_ratio_min", line)

    def test_a_change_that_behaved_is_not_pushed_at_the_reader(self):
        self.assertFalse(any("与预期相反" in line for line in decisions(teacher_report(), [change()])))

    def test_a_session_with_nothing_to_decide_says_nothing(self):
        self.assertEqual(decisions(None, []), [])


class DigestTests(unittest.TestCase):
    def digest(self, *, teacher=True, modes=None):
        return build(date(2026, 9, 22), teacher=teacher_report() if teacher else None,
                     xiaojie=modes if modes is not None else [mode("supplement_rotation"), mode("reverse_wrap")],
                     changes=[change()])

    def test_a_mode_with_too_few_observations_is_shown_but_never_ranked(self):
        digest = self.digest(modes=[mode("thin", observations=MIN_OBSERVATIONS - 1), mode("solid")])
        self.assertEqual(len(digest["xiaojie"]["modes"]), 2)
        self.assertEqual([row["mode"] for row in digest["xiaojie"]["ranked"]], ["solid"])

    def test_the_message_puts_decisions_before_the_scoreboard(self):
        text = digest_text(date(2026, 9, 22), self.digest())
        self.assertLess(text.index("最大漏网"), text.index("老师计划："))
        self.assertIn("研究记录，不构成交易指令。", text)

    def test_the_message_survives_a_session_without_a_teacher_review(self):
        text = digest_text(date(2026, 9, 22), self.digest(teacher=False))
        self.assertNotIn("老师计划：", text)
        self.assertIn("小杰·", text)

    def test_the_page_states_the_measurement_once_at_the_top(self):
        page = digest_markdown(date(2026, 9, 22), self.digest())
        self.assertIn("扣一次往返成本", page.split("## ")[0])
        self.assertIn("封板价触发的不计入胜率", page.split("## ")[0])

    def test_the_page_carries_every_strategys_table(self):
        page = digest_markdown(date(2026, 9, 22), self.digest())
        self.assertIn("## 老师计划", page)
        self.assertIn("platform_breakout", page)
        self.assertIn("supplement_rotation", page)
        self.assertIn("## 改动跟踪", page)

    def test_a_review_note_points_at_the_sweep_before_the_edit(self):
        page = digest_markdown(date(2026, 9, 22), self.digest())
        self.assertIn("sweep", page)
        self.assertIn("变更台账", page)


if __name__ == "__main__":
    unittest.main()
