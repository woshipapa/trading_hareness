"""The radar beside the all-A capture: restore after a restart, limit prices, and the day's read."""

from __future__ import annotations

import asyncio
import unittest
from contextlib import contextmanager
from datetime import date, datetime
from types import SimpleNamespace

from app.market_radar import CN_TZ, RadarState, radar_point
from app.market_radar_runtime import (
    BOARD_FLOW_UNIT, CAPABILITY, PROVIDER_KEY, MarketRadarDependencies, MarketRadarRuntime, radar_day,
)

DAY = date(2026, 10, 9)


def at(hour, minute):
    return datetime(2026, 10, 9, hour, minute, tzinfo=CN_TZ)


class _Connection:
    def __init__(self, points=(), limits=(), flows=()):
        self.points, self.limits, self.flows = list(points), list(limits), list(flows)
        self.queries = []

    def execute(self, sql, params):
        self.queries.append((" ".join(sql.split()), params))
        if "raw_market_observations" in sql:
            rows = [{"effective_at": None, "normalized": point} for point in self.points]
        elif "daily_trade_limits" in sql:
            rows = self.limits
        else:
            rows = self.flows
        return SimpleNamespace(fetchall=lambda: rows)


class _Database:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def transaction(self):
        yield self.connection


def runtime(connection, stored):
    async def run(action, *args, **_kwargs):
        return action(*args)

    def persist(provider, capability, rows):
        stored.append((provider, capability, rows))
        return len(rows)

    return MarketRadarRuntime(MarketRadarDependencies(run_database=run, database=_Database(connection),
                                                      persist_timed_observations=persist))


class RuntimeTests(unittest.TestCase):
    def test_a_restart_restores_the_day_s_bands_before_its_first_new_point(self):
        earlier = radar_point([{"ts_code": "600001.SH", "pct_change": 2.5, "turnover": 10.0}],
                              RadarState(DAY), observed_at=at(9, 31))
        stored = []
        radar = runtime(_Connection(points=[earlier]), stored)
        result = asyncio.run(radar.observe(at(9, 40), [{"ts_code": "600001.SH", "pct_change": 0.5, "turnover": 30.0}]))
        self.assertEqual(result["status"], "stored")
        provider, capability, rows = stored[0]
        self.assertEqual((provider, capability), (PROVIDER_KEY, CAPABILITY))
        self.assertEqual(rows[0]["bands"]["2"]["cum_up"]["turnover"], 30.0, "membership survived the restart")
        self.assertEqual(rows[0]["effective_at"], at(9, 40).isoformat())

    def test_the_session_s_published_limits_add_the_limit_band(self):
        stored = []
        connection = _Connection(limits=[{"symbol": "600001.SH", "limit_up": 11.0, "limit_down": 9.0}])
        radar = runtime(connection, stored)
        asyncio.run(radar.observe(at(10, 0), [{"ts_code": "600001.SH", "pct_change": 10.0, "turnover": 5.0,
                                               "price": 11.0}]))
        self.assertEqual(stored[0][2][0]["bands"]["limit"]["now_up"]["count"], 1)
        # Read once per day, not per point.
        asyncio.run(radar.observe(at(10, 1), [{"ts_code": "600001.SH", "pct_change": 10.0, "turnover": 6.0,
                                               "price": 11.0}]))
        self.assertEqual(sum("daily_trade_limits" in sql for sql, _ in connection.queries), 1)


class ReadTests(unittest.TestCase):
    def test_the_day_reads_its_points_and_sums_industry_boards_into_main_net(self):
        point = radar_point([{"ts_code": "600001.SH", "pct_change": 2.5, "turnover": 10.0}],
                            RadarState(DAY), observed_at=at(9, 31))
        flows = [{"snapshot_minute": at(9, 31), "observed_at": at(9, 31), "status": "completed", "payload": {
            "providers": {"industry": "eastmoney_free", "concept": "eastmoney_free"},
            "items": [{"taxonomy_key": "eastmoney_industry", "net_inflow": -1.5},
                      {"taxonomy_key": "eastmoney_industry", "net_inflow": 0.5},
                      {"taxonomy_key": "eastmoney_concept", "net_inflow": 9.0}]}}]
        day = radar_day(_Connection(points=[point], flows=flows), DAY)
        self.assertEqual(len(day["points"]), 1)
        self.assertNotIn("entered", day["points"][0])
        self.assertEqual(day["main_net"], [{"observed_at": at(9, 31).isoformat(), "main_net": -1.0 * BOARD_FLOW_UNIT,
                                            "boards": 2, "source": "eastmoney_free", "status": "completed"}])


class WiringTests(unittest.TestCase):
    def test_the_service_serves_the_radar_and_feeds_it_from_the_all_a_capture(self):
        import inspect

        import app.main as main

        self.assertIn("/api/v1/market/radar", main.app.openapi()["paths"])
        self.assertIsInstance(main.market_radar, MarketRadarRuntime)
        self.assertIn("on_persisted=lambda observed_at, rows: market_radar.observe(observed_at, rows)",
                      inspect.getsource(main.all_a_level1_snapshot_capture_loop))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
