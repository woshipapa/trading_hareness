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


class _Connection:
    def execute(self, *_args, **_kwargs):
        class Result:
            def fetchall(self_inner): return []
        return Result()


class _Database:
    def transaction(self):
        class Context:
            def __enter__(self_inner): return _Connection()
            def __exit__(self_inner, *exc): return False
        return Context()


class RefreshTests(unittest.TestCase):
    def test_refresh_keeps_the_latest_scored_readings_and_completes_only_with_the_session(self):
        end = date(2099, 3, 10)
        readings = [_reading(end - timedelta(days=i), None if i > 40 else 50.0 + i) for i in range(60, -1, -1)]
        stored = []
        with mock.patch.object(runtime, "compute", return_value=readings), \
                mock.patch.object(runtime, "persist_timed_observations",
                                  side_effect=lambda _db, provider, capability, rows: stored.extend(rows) or len(rows)):
            result = runtime.refresh(_Database(), end, keep=5)
        self.assertEqual((result["status"], result["stored"], result["latest"]["trade_date"]), ("completed", 5, end.isoformat()))
        self.assertEqual([row["trade_date"] for row in stored][-1], end.isoformat())
        self.assertTrue(all(row["effective_at"].endswith("15:00:00+08:00") for row in stored))
        with mock.patch.object(runtime, "compute", return_value=readings[:-1]), \
                mock.patch.object(runtime, "persist_timed_observations", return_value=0):
            self.assertEqual(runtime.refresh(_Database(), end, keep=5)["status"], "blocked")


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
            (runtime.session_close(end - timedelta(days=2)),)).fetchone()["n"]
        self.assertEqual(count, 4)
        self.assertEqual(series["thresholds"], {"freezing": 20.0, "boiling": 80.0})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
