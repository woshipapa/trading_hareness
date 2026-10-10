"""The batched market-flow rebuild writes exactly what the live per-minute path writes, and stops within its budget."""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime, time, timedelta, timezone
from unittest import mock

from psycopg.types.json import Json

from app import market_flow_repository as repository

CHINA = repository.CHINA
DAY = date(2099, 3, 4)


def _items(seed: int) -> list[dict]:
    flows = [((seed * 7 + index * 13) % 23) - 11 for index in range(12)]
    return [{"taxonomy_key": "eastmoney_concept", "net_inflow": flow * 1e6, "change_pct": flow / 10}
            for flow in flows] + [{"taxonomy_key": "industry", "net_inflow": 5.0}]


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class RebuildEquivalenceTests(unittest.TestCase):
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
        # The morning from 09:25 (before the session start) and the afternoon, with one failed minute.
        moments = [time(9, 25 + i) for i in range(12)] + [time(13, 1 + i) for i in range(14)]
        self.minutes = []
        for index, moment in enumerate(moments):
            minute = datetime.combine(DAY, moment, CHINA).astimezone(timezone.utc)
            status = "failed" if index == 15 else ("partial" if index % 5 == 0 else "completed")
            connection.execute(
                """INSERT INTO quant.intraday_board_flow_snapshots(snapshot_minute,observed_at,status,payload)
                   VALUES(%s,%s,%s,%s)""",
                (minute, minute + timedelta(seconds=20), status, Json({"items": _items(index)})))
            if status != "failed":
                self.minutes.append((minute, minute + timedelta(seconds=20)))
        # A stored afternoon feature of a minute the rebuild does not recompute still counts for the minimum.
        stray = datetime.combine(DAY, time(13, 0, 30), CHINA).astimezone(timezone.utc)
        repository._insert_feature(
            connection, feature_key="minute:stray", exchange_date=DAY, cadence="minute", observed_at=stray,
            source_snapshot_minute=None, status="ready", market_state="x",
            features={"board_count": 3, "positive_ratio": 0.01, "quality_flags": []})

    def tearDown(self) -> None:
        self.connection.rollback()
        self.connection.close()

    def _features(self) -> dict[str, tuple]:
        rows = self.connection.execute(
            """SELECT feature_key,status,market_state,concept_positive_ratio,features FROM quant.market_flow_feature_snapshots
                WHERE exchange_date=%s AND cadence='minute' AND feature_key<>'minute:stray' ORDER BY feature_key""",
            (DAY,)).fetchall()
        return {row["feature_key"]: (row["status"], row["market_state"], row["concept_positive_ratio"], row["features"])
                for row in rows}

    def test_the_batched_rebuild_matches_the_live_path_minute_for_minute(self) -> None:
        for minute, observed in self.minutes:
            repository.persist_intraday_market_flow_feature(self.database, minute, observed)
        live = self._features()
        self.connection.execute("""DELETE FROM quant.market_flow_feature_snapshots
                                    WHERE exchange_date=%s AND feature_key<>'minute:stray'""", (DAY,))
        result = repository.rebuild_stored_market_flow_features(self.database, DAY, DAY)
        rebuilt = self._features()
        self.assertEqual(len(live), len(self.minutes))
        self.assertEqual(rebuilt, live)
        self.assertEqual((result["status"], result["minutes_written"], result["minute_rows"]),
                         ("completed", len(self.minutes), len(self.minutes)))
        afternoon = [value for key, value in live.items() if "T05:" in key]
        self.assertTrue(any(value[3] is not None for value in afternoon), "the afternoon minimum path was exercised")

    def test_the_rebuild_stops_between_batches_once_its_budget_is_spent(self) -> None:
        ticks = iter(range(0, 10_000, 100))     # the first check after a batch is past 75 s
        with mock.patch.object(repository, "REBUILD_BATCH", 10):
            result = repository.rebuild_stored_market_flow_features(
                self.database, DAY, DAY, budget_seconds=75, clock=lambda: float(next(ticks)))
        self.assertEqual((result["status"], result["minutes_written"]), ("partial", 10))
        self.assertIn("budget", result["reason"])
        self.assertEqual(len(self._features()), 10, "only whole batches are written")
        self.assertIsNone(result["sector_daily"], "nothing after the minutes runs once the budget is spent")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
