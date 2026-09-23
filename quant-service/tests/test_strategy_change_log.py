"""A change is judged against what we expected before we knew the answer."""

from __future__ import annotations

import contextlib
import unittest
from datetime import date

from app.strategy_change_log import (
    DEFAULT_REVIEW_SESSIONS, change_id, change_lines, evaluate, record, validate,
)


def change(**overrides):
    base = {
        "strategy": "teacher_review", "scope": "platform_breakout", "parameter": "vol_ratio_min",
        "from": 1.5, "to": 1.2, "reason": "近 20 份复盘里这条卡住了 3 次大涨",
        "applied_on": "2026-09-23", "status": "applied",
        "expectation": {"measure": "missed_rate_pct", "direction": "down", "review_after_sessions": 3},
    }
    return {**base, **overrides}


class ValidationTests(unittest.TestCase):
    def test_a_well_formed_change_is_accepted(self):
        self.assertEqual(validate(change()), [])

    def test_a_change_without_a_preregistered_expectation_is_refused(self):
        problems = validate({**change(), "expectation": None})
        self.assertIn("expectation is required: name the measure, the direction and the window", problems)

    def test_the_expectation_must_name_a_measure_the_review_reports(self):
        problems = validate(change(expectation={"measure": "vibes", "direction": "up"}))
        self.assertTrue(any("expectation.measure" in problem for problem in problems))

    def test_a_null_side_is_allowed_but_the_keys_are_not_optional(self):
        self.assertEqual(validate(change(**{"from": None})), [])
        problems = validate({key: value for key, value in change().items() if key != "to"})
        self.assertIn("from and to are required, even when one of them is null", problems)

    def test_the_id_is_the_content_and_ignores_when_it_was_written(self):
        self.assertEqual(change_id(change()), change_id({**change(), "recorded_at": "2026-09-23T10:00:00Z"}))
        self.assertNotEqual(change_id(change()), change_id(change(to=1.3)))


class RecordTests(unittest.TestCase):
    class Database:
        def __init__(self):
            self.statements = []

        @contextlib.contextmanager
        def transaction(self):
            database = self

            class Connection:
                def execute(self, sql, params=None):
                    database.statements.append((sql, params))
            yield Connection()

    def test_a_rejected_change_never_reaches_the_archive(self):
        database = self.Database()
        result = record(database, {**change(), "expectation": None})
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(database.statements, [])

    def test_an_accepted_change_is_archived_as_an_observation(self):
        database = self.Database()
        result = record(database, change())
        self.assertEqual(result["status"], "recorded")
        sql, params = database.statements[0]
        self.assertIn("quant.raw_market_observations", sql)
        self.assertEqual(params[0], "strategy_ops")
        self.assertEqual(params[2], "strategy:teacher_review")


def report(trade_date, *, missed_rate):
    return {"trade_date": trade_date,
            "learning": {"playbooks": [{"playbook": "platform_breakout", "missed_rate_pct": missed_rate}]}}


class EvaluateTests(unittest.TestCase):
    def reports(self, before, after):
        rows = [report(f"2026-09-{day:02d}", missed_rate=value) for day, value in before]
        rows += [report(f"2026-09-{day:02d}", missed_rate=value) for day, value in after]
        return rows

    def test_sessions_before_the_change_can_never_count_as_evidence_for_it(self):
        result = evaluate([change()], self.reports([(21, 60.0), (22, 50.0)],
                                                   [(23, 30.0), (24, 20.0), (25, 10.0)]))[0]
        self.assertEqual(result["sessions_before"], 2)
        self.assertEqual(result["sessions_after"], 3)
        self.assertEqual(result["before"], 55.0)
        self.assertEqual(result["after"], 20.0)

    def test_a_change_that_moved_its_measure_the_promised_way_reads_as_expected(self):
        result = evaluate([change()], self.reports([(22, 50.0)], [(23, 30.0), (24, 20.0), (25, 10.0)]))[0]
        self.assertEqual(result["verdict"], "as_expected")

    def test_a_change_that_moved_it_the_other_way_says_so(self):
        result = evaluate([change()], self.reports([(22, 20.0)], [(23, 40.0), (24, 50.0), (25, 60.0)]))[0]
        self.assertEqual(result["verdict"], "against_expectation")

    def test_nothing_is_concluded_before_the_promised_window(self):
        result = evaluate([change()], self.reports([(22, 50.0)], [(23, 10.0)]))[0]
        self.assertEqual(result["verdict"], "too_early")
        self.assertEqual(result["sessions_after"], 1)

    def test_a_proposed_change_is_not_evaluated_at_all(self):
        self.assertEqual(evaluate([change(status="proposed")], self.reports([(22, 50.0)], [(23, 10.0)])), [])

    def test_the_default_window_is_long_enough_to_mean_something(self):
        self.assertGreaterEqual(DEFAULT_REVIEW_SESSIONS, 10)

    def test_the_report_line_states_expectation_and_result_together(self):
        line = change_lines(evaluate([change()], self.reports([(22, 50.0)], [(23, 30.0), (24, 20.0), (25, 10.0)])))[0]
        self.assertIn("vol_ratio_min 1.5→1.2", line)
        self.assertIn("预期 missed_rate_pct down", line)
        self.assertIn("符合预期", line)


if __name__ == "__main__":
    unittest.main()
