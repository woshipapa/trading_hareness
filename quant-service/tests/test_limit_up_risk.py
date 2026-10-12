from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

from app.limit_up_risk import fifth_comparison, score_day
from app.limit_up_risk_repository import read_inputs, read_lists
from app.routers.market_radar_reads import build_market_radar_router


FIXTURE = json.loads(Path("tests/fixtures/limit_up_risk_20260918.json").read_text(encoding="utf-8"))


class LimitUpRiskTests(unittest.TestCase):
    def test_fixture_parity(self):
        actual = score_day(FIXTURE["lists"], FIXTURE["bars"])
        expected = {row["symbol"]: row for row in FIXTURE["expected"]}
        self.assertEqual(len(actual["rows"]), 76)
        self.assertEqual(actual["coverage"]["malformed"], 2)
        self.assertEqual(actual["coverage"]["excluded_code"], 1)
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

    def test_fifth_comparison_skips_thin_days_and_handles_missing_next_session(self):
        rows = [{"symbol": f"000{i:03d}.SZ", "risk": float(i)} for i in range(20)]
        comparison = fifth_comparison(rows, {row["symbol"]: (-0.06 if i == 0 else 0.01) for i, row in enumerate(rows)})
        self.assertEqual(comparison["lowest"]["big_loss_rate"], 0.25)
        self.assertIsNone(fifth_comparison(rows[:19], {}))
        self.assertIsNone(fifth_comparison(rows, {}))

    def test_repository_bar_sql_has_unit_filter_limit_resolution_and_bounded_history(self):
        statements = []

        class Result:
            def fetchall(self):
                return []

        class Connection:
            def execute(self, sql, _params):
                statements.append(sql)
                return Result()

        read_inputs(Connection(), date(2026, 9, 18))
        joined = "\n".join(statements)
        self.assertIn("amount / (b.volume * b.close / 10.0)", joined)
        self.assertIn("NOT coalesce(b.is_suspended, false)", joined)
        self.assertIn("b.pre_close > 0 AND b.open > 0", joined)
        self.assertIn("coalesce(nullif(b.limit_up, 0), limits.limit_up)", joined)
        self.assertIn("interval '90 days'", joined)

    def test_market_radar_router_exposes_daily_and_track_routes(self):
        paths = {route.path for route in build_market_radar_router(object(), lambda *_args, **_kwargs: None).routes}
        self.assertIn("/api/v1/market/limit-up-risk/daily", paths)
        self.assertIn("/api/v1/market/limit-up-risk/track", paths)


if __name__ == "__main__":
    unittest.main()
