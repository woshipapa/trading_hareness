from datetime import date, timedelta
import unittest

from app.longhu_replay_readiness import REQUIRED_CONTRACTS, assess


def _rows(days: int):
    start = date(2026, 1, 2)
    return [
        {"exchange_date": str(start + timedelta(days=index)), "target": target, "action": action}
        for index in range(days)
        for target, action in REQUIRED_CONTRACTS
    ]


class LonghuReplayReadinessTests(unittest.TestCase):
    def test_readiness_accumulates_without_declaring_strategy_readiness(self):
        result = assess(_rows(60))
        self.assertEqual(result["status"], "accumulating")
        self.assertEqual(result["windows"][1]["status"], "ready")
        self.assertEqual(result["walk_forward"]["status"], "blocked")
        self.assertEqual(result["live_effect"], "none")

    def test_readiness_emits_purged_walk_forward_folds_after_90_complete_sessions(self):
        result = assess(_rows(90))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["windows"][2]["complete_sessions"], 90)
        self.assertEqual(len(result["walk_forward"]["folds"]), 1)
        self.assertEqual(result["walk_forward"]["folds"][0]["embargo_sessions"], 5)

    def test_missing_one_contract_fails_closed_for_its_session(self):
        rows = _rows(30)
        rows = [row for row in rows if not (row["exchange_date"] == "2026-01-31" and row["action"] == "InfoList")]
        result = assess(rows)
        self.assertEqual(result["windows"][0]["complete_sessions"], 29)
