from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import tactic_trial_runtime as runtime
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


class StoredReadTests(unittest.TestCase):
    def _patched(self, stored):
        def read(_connection, capability, first, last):
            return [item for item in stored.get(capability, []) if first.isoformat() <= item["trade_date"] <= last.isoformat()]
        return mock.patch.object(runtime.tactic_trial_repository, "read_capability", side_effect=read)

    def test_the_board_takes_the_close_picks_of_the_session_before_and_the_labels_known_that_morning(self):
        auction = {"trade_date": "2026-10-14", "slot": "auction", "version": VERSION, "decided_at": "2026-10-14T09:25:03+08:00",
                   "source": "auction_open_desk_daily", "day": {"flags": [], "unknown": []}, "rows": []}
        summaries = [{"trade_date": day, "version": VERSION, "records": {"relay_2_3": {"label": label, "q": 0.2}}}
                     for day, label in (("2026-10-13", "collecting"), ("2026-10-14", "supports"))]
        stored = {runtime.AUCTION_CAPABILITY: [auction], runtime.CLOSE_CAPABILITY: [close_reading("2026-10-13")],
                  runtime.SUMMARY_CAPABILITY: summaries}
        with self._patched(stored), \
             mock.patch.object(runtime.tactic_trial_repository, "read_open_sessions", return_value=[date(2026, 10, 12), date(2026, 10, 13)]):
            board = runtime.read_board(object(), date(2026, 10, 14))
        self.assertEqual((board["trade_date"], board["auction"]["decided_at"]), ("2026-10-14", "2026-10-14T09:25:03+08:00"))
        self.assertEqual((board["close"]["trade_date"], board["close"]["focus"][0]["tactics"]), ("2026-10-13", ["relay_2_3"]))
        self.assertEqual(board["labels_as_of"], "2026-10-13", "the evening summary of the same day is not known at 09:25")
        relay = next(item for item in board["tactics"] if item["key"] == "relay_2_3")
        self.assertEqual((relay["label"], relay["side"], relay["slot"]), ("collecting", "focus", "close"))
        self.assertEqual(len(board["tactics"]), 20)
        self.assertEqual((board["research_only"], board["live_effect"]), (True, "none"))

    def test_the_board_of_a_day_without_readings_is_empty_not_an_error(self):
        with self._patched({}), mock.patch.object(runtime.tactic_trial_repository, "read_open_sessions", return_value=[]):
            board = runtime.read_board(object(), date(2026, 10, 14))
        self.assertEqual((board["auction"], board["close"], board["labels_as_of"]), (None, None, None))

    def test_cases_are_the_pick_days_newest_first_and_an_unsettled_day_has_no_outcome(self):
        days = ["2026-10-12", "2026-10-13"]
        stored = {runtime.CLOSE_CAPABILITY: [close_reading(day) for day in days],
                  runtime.SETTLED_CAPABILITY["close"]: [settled("2026-10-12")]}
        with self._patched(stored), \
             mock.patch.object(runtime.tactic_trial_repository, "read_open_sessions", return_value=[date.fromisoformat(day) for day in days]):
            cases = runtime.read_cases(object(), runtime.tactic_by_key("relay_2_3"), 10)
        self.assertEqual([(day["trade_date"], day["settled"], day["picks"][0]["primary"]) for day in cases["days"]],
                         [("2026-10-13", False, None), ("2026-10-12", True, True)])
        self.assertEqual((cases["tactic"]["name"], cases["sessions"]), ("二三板接力", 2))

    def test_the_summary_read_returns_the_stored_summary_of_the_current_version_only(self):
        stored = {runtime.SUMMARY_CAPABILITY: [{"trade_date": "2026-10-13", "version": "older", "lines": []}]}
        with self._patched(stored):
            self.assertIsNone(runtime.read_summary(object(), date(2026, 10, 13))["reading"])


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
        self.assertEqual(seen, [("board", date(2026, 10, 14)), ("summary", None), ("cases", runtime.tactic_by_key("relay_2_3"), 5)])


if __name__ == "__main__":
    unittest.main()
