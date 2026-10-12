from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

from app.limit_up_risk import score_day
from app.limit_up_risk_repository import read_lists


FIXTURE = json.loads(Path("tests/fixtures/limit_up_risk_20260918.json").read_text(encoding="utf-8"))


class LimitUpRiskTests(unittest.TestCase):
    def test_fixture_parity(self):
        actual = score_day(FIXTURE["lists"], FIXTURE["bars"])
        expected = {row["symbol"]: row for row in FIXTURE["expected"]}
        self.assertEqual(len(actual["rows"]), 76)
        self.assertEqual(actual["coverage"]["malformed"], 2)
        self.assertEqual(actual["coverage"]["missing_bar"], 1)
        self.assertEqual(actual["coverage"]["insufficient_history"], 1)
        for row in actual["rows"]:
            for key, value in expected[row["symbol"]].items():
                if key in {"symbol", "sealed_from_open"}:
                    self.assertEqual(row[key], value)
                else:
                    self.assertAlmostEqual(float(row[key]), float(value), places=9, msg=f"{row['symbol']} {key}")

    def test_ties_and_small_days(self):
        lists = {f"pid:{pid}": [] for pid in "12345"}
        lists["pid:1"] = [["000001", "A", 0, "", 0, "S", 1, 1, 1, 1, 0, 2, "", 1, 1, 1, 0, 0, "", "", 0, 10, 10],
                            ["000002", "B", 0, "", 0, "S", 1, 1, 1, 1, 0, 2, "", 1, 1, 1, 0, 0, "", "", 0, 10, 10]]
        bars = {f"00000{i}.SZ": {"open": 10, "close": 11, "pre_close": 10, "amount": 10,
                                  "limit_up": 11, "prior_amounts": [10] * 10} for i in (1, 2)}
        result = score_day(lists, bars)
        self.assertFalse(result["ranks_reliable"])
        self.assertEqual([row["risk_rank"] for row in result["rows"]], [75.0, 75.0])

    def test_history_lists_win_when_any_history_list_is_non_empty(self):
        class Result:
            def __init__(self, rows): self.rows = rows
            def fetchall(self): return self.rows

        class Connection:
            def execute(self, _sql, params):
                if params[0] == "longhuvip_history":
                    return Result([{"symbol": "pid:1", "payload": {"rows": [["history"]]}}])
                return Result([{"symbol": "pid:1", "payload": {"rows": [["live"]]}}])

        lists, source = read_lists(Connection(), date(2026, 9, 18))
        self.assertEqual(source, "history")
        self.assertEqual(lists["pid:1"], [["history"]])


if __name__ == "__main__":
    unittest.main()
