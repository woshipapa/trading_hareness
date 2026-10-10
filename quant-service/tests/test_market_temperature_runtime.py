"""The temperature runtime: what refresh stores, when it is complete, and how the stored series reads back."""

from __future__ import annotations

import os
import unittest
from datetime import date, timedelta
from unittest import mock

from app import market_temperature_runtime as runtime


def _reading(day: date, temperature: float | None, band: str | None = "中性") -> dict:
    return {"trade_date": day.isoformat(), "temperature": temperature, "band": band if temperature is not None else None,
            "scores": {}, "values": {}, "index_close": 3900.0, "limits_ok": True}


class _Database:
    def transaction(self):
        class Context:
            def __enter__(self_inner): return object()
            def __exit__(self_inner, *exc): return False
        return Context()


class RefreshTests(unittest.TestCase):
    def test_the_lookback_gives_every_re_stored_reading_a_full_ranking_window(self):
        from app.market_temperature import TURNOVER_BASE, WINDOW
        # Sessions are about 5/7 of calendar days, less about 15 holidays a year. The margin of 30
        # covers sessions where a component is unknown (no limit prices, no prior limit-ups).
        sessions = runtime.LOOKBACK_DAYS * 5 / 7 - 15 * runtime.LOOKBACK_DAYS / 365
        self.assertGreaterEqual(sessions, WINDOW + runtime.KEEP_SESSIONS + TURNOVER_BASE + 30,
                                "the oldest reading re-stored each evening must still see a full window")

    def test_refresh_keeps_the_latest_scored_readings_and_completes_only_with_the_session(self):
        end = date(2099, 3, 10)
        readings = [_reading(end - timedelta(days=i), None if i > 40 else 50.0 + i) for i in range(60, -1, -1)]
        stored = []
        with mock.patch.object(runtime, "compute", return_value=readings), \
                mock.patch.object(runtime.derived_daily_readings, "store",
                                  side_effect=lambda _db, capability, rows: stored.extend(rows) or {"stored": len(rows), "unchanged": 0}):
            result = runtime.refresh(_Database(), end, keep=5)
        self.assertEqual((result["status"], result["stored"], result["latest"]["trade_date"]), ("completed", 5, end.isoformat()))
        self.assertEqual(stored[-1]["trade_date"], end.isoformat())
        self.assertTrue(all(row["live_effect"] == "none" and row["version"] for row in stored))
        with mock.patch.object(runtime, "compute", return_value=readings[:-1]), \
                mock.patch.object(runtime.derived_daily_readings, "store", return_value={"stored": 0, "unchanged": 5}):
            self.assertEqual(runtime.refresh(_Database(), end, keep=5)["status"], "blocked")


class MarkerTests(unittest.TestCase):
    def test_cold_with_a_surge_is_marked_and_an_index_fall_makes_it_counter_trend(self):
        self.assertEqual(runtime.marker(35.0, 1.6, 0.4)["label"], "冰点资金共振")
        self.assertEqual(runtime.marker(12.0, 1.5, -1.2), {"kind": "cold_with_etf_surge", "label": "逆势放量",
                                                          "counter_trend": True})
        self.assertIsNone(runtime.marker(41.0, 3.0, -1.0), "warmer than cold")
        self.assertIsNone(runtime.marker(15.0, 1.49, -1.0), "no surge")
        self.assertIsNone(runtime.marker(15.0, None, -1.0), "no flow reading")


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class StoredSeriesTests(unittest.TestCase):
    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row
        self.connection = psycopg.connect(
            host=os.getenv("PGHOST"), port=os.getenv("PGPORT", "5432"), dbname=os.getenv("PGDATABASE", "n8n"),
            user=os.getenv("PGUSER", "n8n"), password=os.getenv("PGPASSWORD", ""), row_factory=dict_row,
        )
        connection = self.connection

        class Database:
            def transaction(self):
                class Context:
                    def __enter__(self_inner): return connection
                    def __exit__(self_inner, *exc): return False
                return Context()
        self.database = Database()

    def tearDown(self) -> None:
        self.connection.rollback()
        self.connection.close()

    def test_the_newest_reading_of_each_session_reads_back_in_order(self) -> None:
        end = date(2099, 3, 10)
        first = [_reading(end - timedelta(days=2), 30.0, "冷"), _reading(end - timedelta(days=1), 15.0, "冰点"), _reading(end, 70.0, "热")]
        with mock.patch.object(runtime, "compute", return_value=first):
            runtime.refresh(self.database, end)
            runtime.refresh(self.database, end)          # identical readings are not stored twice
        repaired = first[:-1] + [_reading(end, 72.5, "热")]
        with mock.patch.object(runtime, "compute", return_value=repaired):
            runtime.refresh(self.database, end)
        series = runtime.daily_series(self.connection, days=10, end=end)
        self.assertEqual([(item["trade_date"], item["temperature"]) for item in series["readings"]],
                         [((end - timedelta(days=2)).isoformat(), 30.0), ((end - timedelta(days=1)).isoformat(), 15.0),
                          (end.isoformat(), 72.5)])
        count = self.connection.execute(
            """SELECT count(*) AS n FROM quant.raw_market_observations
                WHERE provider_key='local_derived' AND capability='market_temperature_daily' AND effective_at >= %s""",
            (runtime.derived_daily_readings.session_close(end - timedelta(days=2)),)).fetchone()["n"]
        self.assertEqual(count, 4)
        self.assertEqual((series["thresholds"]["freezing"], series["thresholds"]["boiling"]), (20.0, 80.0))

    def test_the_series_joins_the_etf_flow_and_marks_a_cold_surge(self) -> None:
        from app import broad_etf_flow, derived_daily_readings, market_timing
        end = date(2099, 3, 10)
        day1, day2 = end - timedelta(days=1), end
        readings = [{**_reading(day1, 45.0), "index_close": 4000.0}, {**_reading(day2, 18.0, "冰点"), "index_close": 3960.0}]
        with mock.patch.object(runtime, "compute", return_value=readings):
            runtime.refresh(self.database, end)
        derived_daily_readings.store(self.database, broad_etf_flow.FLOW_CAPABILITY, [
            {"trade_date": day1.isoformat(), "ratio": 2.0, "codes": 12}, {"trade_date": day2.isoformat(), "ratio": 1.8, "codes": 12}])
        derived_daily_readings.store(self.database, market_timing.CAPABILITY, [
            {"trade_date": day2.isoformat(), "state": "silver", "event": "silver"}])
        series = runtime.daily_series(self.connection, days=10, end=end)
        first, second = series["readings"]
        self.assertIsNone(first["marker"], "45 is not cold, whatever the flow")
        self.assertEqual((first["timing"], second["timing"]), (None, {"state": "silver", "event": "silver"}))
        self.assertEqual((second["index_change_pct"], second["etf_flow"]["ratio"], second["marker"]["label"]),
                         (-1.0, 1.8, "逆势放量"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
