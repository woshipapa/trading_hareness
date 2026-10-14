from __future__ import annotations

import json
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.limit_up_risk import (
    ROUND_TRIP_COSTS, decode_limit_rows, fifth_comparison, open_to_close_status, recent_status, score_day,
)
from app.limit_up_risk_repository import read_inputs, read_lists
from app.routers.market_radar_reads import build_market_radar_router


FIXTURE = json.loads(Path("tests/fixtures/limit_up_risk_20260918.json").read_text(encoding="utf-8"))


class LimitUpRiskTests(unittest.TestCase):
    def test_decoder_filters_malformed_duplicate_and_not_at_limit(self):
        row = ["000001", "A", 0, "", 1_758_200_000, "Theme", 2, 3, 4, 0, 0, 5, "", 6, 0, 2, 1, 0, "", "", 0, 0, 0]
        lists = {f"pid:{pid}": [] for pid in "12345"}
        lists["pid:1"] = [["short"], row, row, ["000002", "B", 0, "", 1_758_200_000, "Theme", 2, 3, 4, 0, 0, 5, "", 6, 0, 2, 1, 0, "", "", 0, 0, 0]]
        bars = {
            "000001.SZ": {"pre_close": 10, "close": 11, "limit_up": 11},
            "000002.SZ": {"pre_close": 10, "close": 10.5, "limit_up": 11},
        }

        decoded = decode_limit_rows(lists, bars)

        self.assertEqual(decoded["coverage"], {
            "malformed": 1, "excluded_code": 0, "duplicate": 1, "missing_bar": 0,
            "limit_ratio": 0, "not_at_limit": 1,
        })
        self.assertEqual(decoded["rows"], [{
            "symbol": "000001.SZ", "name": "A", "sector": "Theme", "first_seal_time": 1_758_200_000,
            "seal_money": 2.0, "seal_max": 3.0, "main_net": 4.0, "turnover": 5.0,
            "float_cap": 6.0, "boards": 2.0, "reopened": 1.0, "bar": bars["000001.SZ"],
        }])

    def test_fixture_parity(self):
        actual = score_day(FIXTURE["lists"], FIXTURE["bars"])
        expected = {row["symbol"]: row for row in FIXTURE["expected"]}
        actual_symbols = [row["symbol"] for row in actual["rows"]]
        self.assertEqual(len(actual_symbols), len(FIXTURE["expected"]))
        self.assertEqual(set(actual_symbols), set(expected))
        self.assertEqual(actual["coverage"]["malformed"], 2)
        self.assertEqual(actual["coverage"], {
            "malformed": 2, "excluded_code": 1, "duplicate": 0, "missing_bar": 0,
            "limit_ratio": 0, "not_at_limit": 0, "insufficient_history": 1,
        })
        actual_by_symbol = {row["symbol"]: row for row in actual["rows"]}
        for symbol, expected_row in expected.items():
            for key, value in expected_row.items():
                if key in {"symbol", "sealed_from_open"}:
                    self.assertEqual(actual_by_symbol[symbol][key], value)
                else:
                    self.assertAlmostEqual(float(actual_by_symbol[symbol][key]), float(value), places=9, msg=f"{symbol} {key}")

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

    def test_repository_returns_complete_t_day_bars_without_prior_only_symbols(self):
        row = ["000001", "A", 0, "", 0, "S", 1, 1, 1, 1, 0, 2, "", 1, 1, 1, 0, 0, "", "", 0, 10, 10]

        class Result:
            def __init__(self, rows): self.rows = rows
            def fetchall(self): return self.rows

        class Connection:
            def __init__(self, t_rows, prior_rows): self.t_rows, self.prior_rows = t_rows, prior_rows
            def execute(self, sql, _params):
                if "raw_market_observations" in sql:
                    return Result([{"symbol": "pid:1", "payload": {"rows": [row]}}])
                if "canonical_bars_daily b" in sql:
                    return Result(self.t_rows)
                return Result(self.prior_rows)

        today = {"symbol": "000001.SZ", "open": 10, "close": 11, "pre_close": 10,
                 "amount": 10, "limit_up": 11}
        self.assertEqual(read_inputs(Connection([today], [{"symbol": "000002.SZ", "amount": 1}]), date(2026, 9, 18))["bars"],
                         {"000001.SZ": {**today, "prior_amounts": []}})
        self.assertEqual(read_inputs(Connection([], [{"symbol": "000001.SZ", "amount": 1}]), date(2026, 9, 18))["bars"], {})

    def test_fifth_comparison_skips_thin_days_and_handles_missing_next_session(self):
        rows = [{"symbol": f"000{i:03d}.SZ", "risk": float(i)} for i in range(20)]
        comparison = fifth_comparison(rows, {row["symbol"]: (-0.06 if i == 0 else 0.01) for i, row in enumerate(rows)})
        self.assertEqual(comparison["lowest"]["big_loss_rate"], 0.25)
        self.assertIsNone(fifth_comparison(rows[:19], {}))
        self.assertIsNone(fifth_comparison(rows, {}))

    def test_recent_status_working_and_not_working(self):
        working = [{"lowest": {"big_loss_rate": 0.08}, "highest": {"big_loss_rate": 0.13 + index % 3 * 0.005}} for index in range(60)]
        self.assertEqual(recent_status(working)["status"], "working")
        not_working = [{"lowest": {"big_loss_rate": 0.10}, "highest": {"big_loss_rate": 0.11}} for _ in range(60)]
        self.assertEqual(recent_status(not_working)["status"], "not_working_recently")

    def test_recent_status_requires_sixty_sessions(self):
        summaries = [{"lowest": {"big_loss_rate": 0.08}, "highest": {"big_loss_rate": 0.13}} for _ in range(59)]
        self.assertEqual(recent_status(summaries)["status"], "insufficient_history")

    def test_recent_status_hides_zero_variance_t_value(self):
        summaries = [{"lowest": {"big_loss_rate": 0.08}, "highest": {"big_loss_rate": 0.13}} for _ in range(60)]
        self.assertIsNone(recent_status(summaries)["t_value"])

    def test_open_to_close_status_reports_the_buyers_net_after_two_cost_sets(self):
        days = [{"lowest": {"big_loss_rate": 0.05, "mean_next_day_return": 0.004},
                 "highest": {"big_loss_rate": 0.12 + index % 3 * 0.005, "mean_next_day_return": -0.006}}
                for index in range(60)]
        result = open_to_close_status(days)
        self.assertEqual(result["status"], "working")
        self.assertEqual(result["basis"], "next_session_open_to_close")
        self.assertAlmostEqual(result["mean_open_to_close"]["highest"], -0.006)
        self.assertEqual(set(result["net_of_round_trip_costs"]), set(ROUND_TRIP_COSTS))
        for name, cost in ROUND_TRIP_COSTS.items():
            self.assertAlmostEqual(result["net_of_round_trip_costs"][name]["highest"], -0.006 - cost)
            self.assertAlmostEqual(result["net_of_round_trip_costs"][name]["lowest"], 0.004 - cost)
        self.assertLess(ROUND_TRIP_COSTS["commission_5bp_slippage_10bp"], ROUND_TRIP_COSTS["commission_5bp_slippage_20bp"])

    def test_open_to_close_status_with_no_sessions_is_insufficient_not_an_error(self):
        result = open_to_close_status([])
        self.assertEqual(result["status"], "insufficient_history")
        self.assertIsNone(result["mean_open_to_close"]["highest"])
        self.assertIsNone(result["net_of_round_trip_costs"]["commission_5bp_slippage_20bp"]["highest"])

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
        self.assertIn("b.close > 0", joined)
        self.assertIn("AND close > 0", joined)
        self.assertIn("coalesce(nullif(b.limit_up, 0), limits.limit_up)", joined)
        self.assertIn("trading_date BETWEEN %s AND %s", joined)
        self.assertIn("DISTINCT ON (symbol)", joined)

    def test_track_close_sql_joins_parallel_symbol_date_pairs(self):
        from app import limit_up_risk_runtime
        class Result:
            def __init__(self, rows): self.rows = rows
            def fetchall(self): return self.rows
        class Connection:
            def __init__(self): self.statements = []; self.params = []
            def execute(self, sql, params):
                self.statements.append(sql); self.params.append(params)
                if "market_trade_calendar" in sql:
                    return Result([{"calendar_date": date(2026, 9, 17), "next_day": date(2026, 9, 18)}])
                if "canonical_bars_daily" in sql:
                    symbols, dates = params
                    # The next session opens above where it closes: its overnight gap is not a buyer's return.
                    return Result([{"symbol": symbol, "trading_date": day, "close": 100.0,
                                    "open": 102.0 if day == date(2026, 9, 18) else 100.0}
                                   for symbol, day in zip(symbols, dates)])
                return Result([])
        class Database:
            def __init__(self, connection): self.connection = connection
            def transaction(self):
                class Tx:
                    def __init__(self, connection): self.connection = connection
                    def __enter__(self): return self.connection
                    def __exit__(self, *_args): return False
                return Tx(self.connection)
        connection = Connection()
        rows = [{"symbol": f"{index:06d}.SZ", "risk": index} for index in range(1, 21)]
        reading = {"trade_date": "2026-09-17", "version": "limit-up-risk-v1", "rows": rows}
        with mock.patch.object(limit_up_risk_runtime.derived_daily_readings, "newest", return_value=[reading]):
            result = limit_up_risk_runtime.track(Database(connection), 3)
        close_sql = next(sql for sql in connection.statements if "canonical_bars_daily" in sql)
        self.assertIn("JOIN unnest", close_sql)
        # The calendar holds the rest of the year: the newest sessions are counted back from today.
        calendar_index = next(i for i, sql in enumerate(connection.statements) if "LIMIT %s" in sql)
        self.assertIn("calendar_date <= %s", connection.statements[calendar_index])
        self.assertEqual(connection.params[calendar_index], (datetime.now(limit_up_risk_runtime.CN_TZ).date(), 3))
        self.assertNotIn("BETWEEN", close_sql)
        symbols, dates = connection.params[-1]
        expected_pairs = [(row["symbol"], day) for row in rows for day in (date(2026, 9, 17), date(2026, 9, 18))]
        self.assertEqual(list(zip(symbols, dates)), expected_pairs)
        intraday = 100.0 / 102.0 - 1
        self.assertEqual(result["days"], [{
            "trade_date": "2026-09-17",
            "lowest": {"big_loss_rate": 0.0, "mean_next_day_return": 0.0},
            "highest": {"big_loss_rate": 0.0, "mean_next_day_return": 0.0},
            "open_to_close": {"lowest": {"big_loss_rate": 0.0, "mean_next_day_return": intraday},
                              "highest": {"big_loss_rate": 0.0, "mean_next_day_return": intraday}},
        }])
        self.assertEqual(result["judged_on"], "next_session_open_to_close")
        self.assertEqual(result["recent_open_to_close"]["basis"], "next_session_open_to_close")
        self.assertAlmostEqual(result["recent_open_to_close"]["mean_open_to_close"]["highest"], intraday)
        self.assertEqual(result["recent"]["sessions"], 1, "the close-to-close block is kept as it was")

    def test_daily_returns_the_requested_session_not_the_oldest_stored_one(self):
        from app import limit_up_risk_runtime
        stored = [{"trade_date": "2024-08-05", "version": limit_up_risk_runtime.VERSION, "rows": ["old"]},
                  {"trade_date": "2026-10-09", "version": limit_up_risk_runtime.VERSION, "rows": ["friday"]},
                  {"trade_date": "2026-10-12", "version": limit_up_risk_runtime.VERSION, "rows": ["monday"]}]

        def newest(_connection, _capability, first, last):
            return [reading for reading in stored if first.isoformat() <= reading["trade_date"] <= last.isoformat()]

        class Database:
            def transaction(self):
                class Tx:
                    def __enter__(self): return object()
                    def __exit__(self, *_args): return False
                return Tx()

        with mock.patch("app.limit_up_risk_runtime.derived_daily_readings.newest", side_effect=newest):
            self.assertEqual(limit_up_risk_runtime.daily(Database(), date(2026, 10, 9))["rows"], ["friday"])
            self.assertIsNone(limit_up_risk_runtime.daily(Database(), date(2026, 10, 10)))
            series = limit_up_risk_runtime.daily_series(object(), date(2026, 10, 12))
        self.assertEqual(series["reading"]["rows"], ["monday"])
        self.assertEqual(series["stale_versions"], 0)

    def test_track_empty_calendar_keeps_recent_shape(self):
        from app import limit_up_risk_runtime
        class Result:
            def fetchall(self): return []
        class Connection:
            def execute(self, _sql, _params): return Result()
        class Database:
            def transaction(self):
                class Tx:
                    def __enter__(self): return Connection()
                    def __exit__(self, *_args): return False
                return Tx()
        result = limit_up_risk_runtime.track(Database(), 3)
        self.assertEqual(result["recent"]["status"], "insufficient_history")

    def test_market_radar_router_exposes_daily_and_track_routes(self):
        paths = {route.path for route in build_market_radar_router(object(), lambda *_args, **_kwargs: None).routes}
        self.assertIn("/api/v1/market/limit-up-risk/daily", paths)
        self.assertIn("/api/v1/market/limit-up-risk/track", paths)

    def test_limit_up_risk_handlers_read_daily_and_track_payloads(self):
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
        client = TestClient(app)
        def daily_series(_connection, requested_date):
            return {"version": "limit-up-risk-v1", "reading": {"trade_date": requested_date.isoformat()} if requested_date else {"trade_date": "2026-09-19"},
                    "stale_versions": 0, "research_only": True, "live_effect": "none"}
        with mock.patch("app.routers.market_radar_reads.limit_up_risk_daily_series", side_effect=daily_series), mock.patch("app.routers.market_radar_reads.limit_up_risk_track", return_value={
            "version": "limit-up-risk-v1", "days": [], "stale_versions": 1,
            "research_only": True, "live_effect": "none",
        }):
            self.assertEqual(client.get("/api/v1/market/limit-up-risk/daily").json()["reading"]["trade_date"], "2026-09-19")
            self.assertEqual(client.get("/api/v1/market/limit-up-risk/daily?date=2026-09-18").json()["reading"]["trade_date"], "2026-09-18")
            self.assertEqual(client.get("/api/v1/market/limit-up-risk/track?days=3").json()["stale_versions"], 1)

    def test_auction_desk_handlers_expose_daily_and_track(self):
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
        client = TestClient(app)
        with mock.patch("app.routers.market_radar_reads.auction_open_desk_daily_series", return_value={"reading": None}), \
             mock.patch("app.routers.market_radar_reads.auction_open_desk_track", return_value={"days": []}):
            self.assertIsNone(client.get("/api/v1/market/auction-desk/daily").json()["reading"])
            self.assertEqual(client.get("/api/v1/market/auction-desk/track?days=3").json()["days"], [])


if __name__ == "__main__":
    unittest.main()
