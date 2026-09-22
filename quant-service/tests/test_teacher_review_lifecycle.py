"""Promote satisfied teacher plans, observe the rest, retire the spent."""

from __future__ import annotations

import unittest

from app.teacher_review_lifecycle import (
    OBSERVE_GRACE_SESSIONS, PROMOTED_MAX_CARRY, close_trigger, continuation_stock, decide,
)

# MA prefixes: sum of the previous n-1 closes, so today's MA = (prefix + close) / n.
PREFIX = {"5": 4 * 10.0, "10": 9 * 9.5}


def plan(playbook, *, params=None, extra=None, lifecycle=None, session_index=1, valid=1):
    return {"playbook": playbook, "params": params or {}, "extra": {"ma_prefix": PREFIX, **(extra or {})},
            "lifecycle": lifecycle or {"state": "new"}, "session_index": session_index, "valid_sessions": valid}


def fact(close, *, high=None, sealed=False, entry=None, invalid=False):
    return {"bar": {"close": close, "high": high or close}, "closed_at_limit": sealed,
            "entry": entry, "invalidated_at": "2026-09-22T14:55:00+08:00" if invalid else None}


class DecideTests(unittest.TestCase):
    def test_a_relay_that_sealed_another_board_is_promoted(self):
        self.assertEqual(decide(plan("relay_race"), fact(11.0, sealed=True))["state"], "promoted")

    def test_a_relay_that_failed_is_observed_unless_it_was_invalidated(self):
        self.assertEqual(decide(plan("relay_race"), fact(10.2))["state"], "observe")
        self.assertEqual(decide(plan("relay_race"), fact(10.2, invalid=True))["state"], "expired")

    def test_a_trend_plan_is_satisfied_by_a_close_through_its_trigger(self):
        breakout = plan("prior_high_breakout", params={"prior_high": 20.0}, valid=2)
        self.assertEqual(decide(breakout, fact(20.5))["reason"], "收盘站上 20")
        self.assertEqual(decide(breakout, fact(19.9))["state"], "observe")
        platform = plan("platform_breakout", extra={"platform_upper": 12.0}, valid=2)
        self.assertEqual(decide(platform, fact(12.1))["state"], "promoted")

    def test_a_confirmed_entry_held_into_the_close_is_promoted(self):
        pullback = plan("leader_benchmark_pullback", valid=2)
        self.assertEqual(decide(pullback, fact(10.4, entry={"price": 10.2}))["state"], "promoted")
        self.assertEqual(decide(pullback, fact(10.0, entry={"price": 10.2}))["state"], "observe")

    def test_observation_ends_after_the_teachers_window_plus_grace(self):
        # A carried plan's own valid_sessions is widened; the teacher's window rules.
        late = plan("platform_breakout", extra={"platform_upper": 12.0}, session_index=1 + OBSERVE_GRACE_SESSIONS,
                    valid=9, lifecycle={"state": "observe", "teacher_valid_sessions": 1})
        self.assertEqual(decide(late, fact(11.0))["state"], "expired")
        early = plan("platform_breakout", extra={"platform_upper": 12.0}, session_index=2,
                     lifecycle={"state": "observe", "teacher_valid_sessions": 2})
        self.assertEqual(decide(early, fact(11.0))["state"], "observe")

    def test_a_promoted_plan_holds_ma5_breaks_to_observe_and_ends_below_ma10(self):
        promoted = plan("trend_continuation", params={"prior_high": 11.0}, lifecycle={"state": "promoted", "carried": 1})
        # MA5 at close 10.5 = (40 + 10.5) / 5 = 10.1; MA10 = (85.5 + 10.5) / 10 = 9.6
        self.assertEqual(decide(promoted, fact(10.5)), {"state": "promoted", "reason": "守住MA5，继续晋级延续", "carried": 2})
        self.assertEqual(decide(promoted, fact(9.9))["state"], "observe")        # MA5 9.98 > 9.9 > MA10 9.54
        self.assertEqual(decide(promoted, fact(9.0))["state"], "expired")        # below MA10
        spent = plan("trend_continuation", lifecycle={"state": "promoted", "carried": PROMOTED_MAX_CARRY - 1})
        self.assertEqual(decide(spent, fact(10.5))["state"], "expired")

    def test_a_session_without_a_bar_changes_nothing(self):
        self.assertEqual(decide(plan("relay_race"), {"bar": None})["state"], "observe")
        kept = decide(plan("trend_continuation", lifecycle={"state": "promoted", "carried": 2}), None)
        self.assertEqual((kept["state"], kept["carried"]), ("promoted", 2))

    def test_the_continuation_plan_confirms_above_the_promotion_day_high(self):
        stock = {"code": "605058", "name": "澳弘电子", "group": "g", "playbook": "platform_breakout",
                 "evidence": [{"time": "1", "quote": "q"}], "teacher": "t"}
        carried = continuation_stock(stock, {"high": 53.27}, "封板晋级")
        self.assertEqual(carried["playbook"], "trend_continuation")
        self.assertEqual(carried["params"]["prior_high"], 53.27)
        self.assertEqual(close_trigger({"playbook": "trend_continuation", "params": carried["params"]}), 53.27)
        self.assertEqual(carried["original_playbook"], "platform_breakout")


if __name__ == "__main__":
    unittest.main()
