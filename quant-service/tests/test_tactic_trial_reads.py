from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import tactic_trial_runtime as runtime   # the stored reads are exercised on a real database in test_tactic_trial_integration
from app.routers.market_radar_reads import build_market_radar_router
from app.tactic_trials import day_board, tactic_day

VERSION = runtime.VERSION


def close_reading(day: str) -> dict:
    rows = [
        {"symbol": "000001.SZ", "name": "接力", "sector": "机器人", "boards": 2, "board_bucket": "2", "gap": 0.02,
         "flags": ["board_2_3", "theme_7plus", "rebound_board"], "unknown": []},
        {"symbol": "000002.SZ", "name": "对照", "sector": "机器人", "boards": 2, "board_bucket": "2", "gap": 0.0, "flags": ["board_2_3"], "unknown": []},
    ]
    return {"trade_date": day, "slot": "close", "version": VERSION, "day": {"flags": [], "unknown": []}, "rows": rows}


def settled(day: str, *, complete: bool = True) -> dict:
    def outcome(value: str, seal: bool) -> dict:
        item = {"status": "settled", "raw_return": Decimal(value), "net_return": Decimal(value)}
        return {"entries": {"held": {"close": item}, "buy_open": {"close": item}}, "seal": seal, "big_loss": False}
    return {"trade_date": day, "slot": "close", "version": VERSION, "complete": complete,
            "rows": [{"symbol": "000001.SZ", "outcomes": outcome("0.05", True)}, {"symbol": "000002.SZ", "outcomes": outcome("-0.01", False)}]}


class PureReadTests(unittest.TestCase):
    def test_the_board_lists_a_stock_under_each_side_whose_combination_picked_it(self):
        board = day_board(close_reading("2026-10-13"))
        self.assertEqual([(row["symbol"], row["tactics"]) for row in board["focus"]], [("000001.SZ", ["relay_2_3"])])
        self.assertEqual([(row["symbol"], row["tactics"]) for row in board["avoid"]], [("000001.SZ", ["rebound_board_risk"])])
        self.assertEqual(board["hold"], [])
        self.assertEqual(board["focus"][0]["sector"], "机器人")
        self.assertNotIn("flags", board["focus"][0])

    def test_a_tactic_day_has_its_picks_their_outcome_and_the_difference_to_the_rest(self):
        tactic = runtime.tactic_by_key("relay_2_3")
        reading, outcomes = close_reading("2026-10-13"), settled("2026-10-13")
        day = tactic_day({**reading, "outcomes": {row["symbol"]: row["outcomes"] for row in outcomes["rows"]}}, tactic)
        self.assertEqual([(pick["symbol"], pick["primary"]) for pick in day["picks"]], [("000001.SZ", True)])
        self.assertEqual((day["pick_mean"], day["comparison_mean"], day["difference"]), (1.0, 0.0, 1.0))


class RouteTests(unittest.TestCase):
    def setUp(self):
        class Database:
            def transaction(self):
                class Tx:
                    def __enter__(self): return object()
                    def __exit__(self, *_args): return False
                return Tx()

        async def run_database(action, *args, **_kwargs):
            return action(*args)

        app = FastAPI()
        app.include_router(build_market_radar_router(Database(), run_database))
        self.client = TestClient(app)

    def test_the_three_routes_pass_the_date_alias_the_tactic_and_the_bounds(self):
        seen = []

        def read(name):
            def run(_connection, *args):
                seen.append((name, *args))
                return {"research_only": True, "live_effect": "none"}
            return run

        with mock.patch("app.routers.market_radar_reads.tactic_trial_board", side_effect=read("board")), \
             mock.patch("app.routers.market_radar_reads.tactic_trial_summary", side_effect=read("summary")), \
             mock.patch("app.routers.market_radar_reads.tactic_trial_cases", side_effect=read("cases")):
            self.assertEqual(self.client.get("/api/v1/market/tactic-trials/board?date=2026-10-14").status_code, 200)
            self.assertEqual(self.client.get("/api/v1/market/tactic-trials/summary").status_code, 200)
            self.assertEqual(self.client.get("/api/v1/market/tactic-trials/cases?tactic=relay_2_3&days=5").status_code, 200)
            self.assertEqual(self.client.get("/api/v1/market/tactic-trials/cases?tactic=not_a_tactic").status_code, 404)
            self.assertEqual(self.client.get("/api/v1/market/tactic-trials/cases?tactic=relay_2_3&days=21").status_code, 422)
            self.assertEqual(self.client.get("/api/v1/market/tactic-trials/cases?tactic=" + "x" * 65).status_code, 422)
            self.assertEqual(self.client.get("/api/v1/market/tactic-trials/cases").status_code, 422)
            self.assertEqual(self.client.get("/api/v1/market/tactic-trials/board?date=0001-01-02").status_code, 422,
                             "a date before the stored history is refused, not passed into the look-back arithmetic")
        self.assertEqual(seen, [("board", date(2026, 10, 14)), ("summary", None), ("cases", runtime.tactic_by_key("relay_2_3"), 5)])


if __name__ == "__main__":
    unittest.main()
