"""Run this change set's new SQL against a real PostgreSQL schema.

Every test works inside one transaction that is rolled back, so it leaves no
rows behind in a shared development database.
"""

from __future__ import annotations

import json
import os
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class PointInTimeAndPaperSqlTests(unittest.TestCase):
    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row
        self.connection = psycopg.connect(
            host=os.getenv("PGHOST"), port=os.getenv("PGPORT", "5432"), dbname=os.getenv("PGDATABASE", "n8n"),
            user=os.getenv("PGUSER", "n8n"), password=os.getenv("PGPASSWORD", ""), row_factory=dict_row,
        )
        for symbol in ("990001.SH", "990002.SH"):
            self.connection.execute(
                "INSERT INTO quant.instruments(symbol,exchange,is_st) VALUES(%s,'SH',%s) ON CONFLICT(symbol) DO UPDATE SET is_st=EXCLUDED.is_st",
                (symbol, symbol == "990002.SH"),
            )

    def tearDown(self) -> None:
        self.connection.rollback()
        self.connection.close()

    def test_st_status_comes_from_the_dated_cross_section_when_covered(self) -> None:
        from app.point_in_time_status import st_flags_as_of
        covered, uncovered = date(2099, 3, 2), date(2099, 3, 3)
        # On the covered session 990001 was ST and 990002 was not - the
        # reverse of today's instrument flags.
        self.connection.execute(
            """INSERT INTO quant.instrument_lifecycle_evidence(symbol,provider,observed_at,status_date,list_status,is_st,available_at)
               VALUES('990001.SH','test',now(),%s,'UNKNOWN',true,now())""", (covered,),
        )
        on_covered = st_flags_as_of(self.connection, ["990001.SH", "990002.SH"], covered)
        self.assertEqual(on_covered["990001.SH"], {"is_st": True, "st_basis": "dated_stock_st"})
        self.assertEqual(on_covered["990002.SH"], {"is_st": False, "st_basis": "dated_stock_st"})
        on_uncovered = st_flags_as_of(self.connection, ["990001.SH", "990002.SH"], uncovered)
        self.assertEqual(on_uncovered["990001.SH"], {"is_st": False, "st_basis": "current_instrument_flag"})
        self.assertEqual(on_uncovered["990002.SH"], {"is_st": True, "st_basis": "current_instrument_flag"})

    def test_a_membership_refresh_and_close_keep_the_first_known_at(self) -> None:
        from app.sector_membership_repository import persist_observed_snapshot
        self.connection.execute(
            """INSERT INTO quant.providers(provider_key,label) VALUES('test_sector','test') ON CONFLICT DO NOTHING""")
        self.connection.execute(
            """INSERT INTO quant.sector_taxonomies(taxonomy_key,label,provider_key)
               VALUES('test_taxonomy','test','test_sector') ON CONFLICT DO NOTHING""")
        self.connection.execute(
            """INSERT INTO quant.sectors(taxonomy_key,sector_key,label)
               VALUES('test_taxonomy','S1','test') ON CONFLICT DO NOTHING""")
        first = datetime(2099, 3, 2, 8, 0, tzinfo=timezone.utc)
        kwargs = {"member_symbol": lambda row: row["code"], "ensure_instrument": lambda *_args: None}
        persist_observed_snapshot(self.connection, "test_taxonomy", "S1",
                                  [{"code": "990001.SH"}, {"code": "990002.SH"}], "test_sector", first, **kwargs)
        # Same session refreshed later, then a later session drops 990002.
        persist_observed_snapshot(self.connection, "test_taxonomy", "S1",
                                  [{"code": "990001.SH"}, {"code": "990002.SH"}], "test_sector", first + timedelta(hours=3), **kwargs)
        persist_observed_snapshot(self.connection, "test_taxonomy", "S1",
                                  [{"code": "990001.SH"}], "test_sector", first + timedelta(days=5), **kwargs)
        rows = {row["symbol"]: row for row in self.connection.execute(
            """SELECT symbol,effective_from,effective_to,known_at FROM quant.sector_membership_history
                WHERE taxonomy_key='test_taxonomy' AND effective_from=%s""", (date(2099, 3, 2),)).fetchall()}
        self.assertEqual(rows["990001.SH"]["known_at"], first)
        self.assertEqual(rows["990002.SH"]["known_at"], first)
        self.assertEqual(rows["990002.SH"]["effective_to"], date(2099, 3, 6))

    def test_a_supplementary_universe_snapshot_never_closes_an_interval(self) -> None:
        from app.universe_history import sync_universe_membership_history
        sync_universe_membership_history(self.connection, "test_universe", date(2099, 3, 2),
                                         ["990001.SH", "990002.SH"], source="authoritative")
        result = sync_universe_membership_history(self.connection, "test_universe", date(2099, 3, 3),
                                                  ["990001.SH"], source="longhu", close_missing=False)
        self.assertEqual(result["closed"], 0)
        open_rows = self.connection.execute(
            """SELECT symbol FROM quant.universe_membership_history
                WHERE universe_key='test_universe' AND effective_to IS NULL ORDER BY symbol""").fetchall()
        self.assertEqual([row["symbol"] for row in open_rows], ["990001.SH", "990002.SH"])

    def test_the_daily_st_capture_statement_writes_dated_evidence(self) -> None:
        # The statement full_market_daily_controls_sync issues, verbatim shape.
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / "app" / "full_market_daily_controls_sync.py").read_text(encoding="utf-8")
        start = source.index('"""INSERT INTO quant.instrument_lifecycle_evidence(')
        statement = source[start + 3:source.index('"""', start + 3)]
        observed_at = datetime(2099, 3, 2, 9, 0, tzinfo=timezone.utc)
        payload = json.dumps([{"symbol": "990001.SH", "raw": {"ts_code": "990001.SH"}},
                              {"symbol": "999999.SH", "raw": {"ts_code": "999999.SH"}}])
        self.connection.execute(statement, ("test", observed_at, date(2099, 3, 2), observed_at, payload))
        rows = self.connection.execute(
            "SELECT symbol,is_st FROM quant.instrument_lifecycle_evidence WHERE provider='test'").fetchall()
        # A code with no instrument row is skipped rather than failing the batch.
        self.assertEqual([(row["symbol"], row["is_st"]) for row in rows], [("990001.SH", True)])

    def test_the_alert_retry_scan_claims_a_row_once(self) -> None:
        from app.intraday_alert_delivery_service import create_pending_delivery, load_due_deliveries

        class Database:
            def __init__(self, connection):
                self.connection = connection

            def transaction(self):
                connection = self.connection

                class Context:
                    def __enter__(self): return connection
                    def __exit__(self, *_args): return False
                return Context()

        signal_event_id = uuid.uuid4()
        self.connection.execute(
            """INSERT INTO quant.intraday_signal_events(signal_event_id,symbol,signal_key,signal_type,severity,state,score,observed_at)
               VALUES(%s,'990001.SH','test','entry','info','confirmed',0,now())""", (signal_event_id,))
        database = Database(self.connection)
        delivery_id = create_pending_delivery(database, signal_event_id, "alert")
        # Inside its first-attempt lease the row is not due.
        self.assertEqual([row for row in load_due_deliveries(database, 3, 10) if row["delivery_id"] == delivery_id], [])
        self.connection.execute(
            "UPDATE quant.intraday_alert_deliveries SET next_attempt_at=now()-interval '1 second' WHERE delivery_id=%s",
            (delivery_id,))
        first = [row for row in load_due_deliveries(database, 3, 10) if row["delivery_id"] == delivery_id]
        second = [row for row in load_due_deliveries(database, 3, 10) if row["delivery_id"] == delivery_id]
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])

    def test_the_paper_snapshot_reads_its_high_water_mark_and_prior_close(self) -> None:
        from app.paper_portfolio import persist_portfolio_snapshot
        self.connection.execute("DELETE FROM quant.paper_portfolio_snapshots WHERE as_of>='2099-01-01'")
        for as_of, equity in ((datetime(2099, 3, 1, 3, 0, tzinfo=timezone.utc), 120_000),
                              (datetime(2099, 3, 1, 6, 59, tzinfo=timezone.utc), 100_000)):
            self.connection.execute(
                """INSERT INTO quant.paper_portfolio_snapshots(as_of,cash,equity,gross_exposure,net_exposure,drawdown,payload)
                   VALUES(%s,%s,%s,0,0,0,'{}')""", (as_of, equity, equity))
        snapshot = persist_portfolio_snapshot(
            self.connection, as_of=datetime(2099, 3, 2, 2, 0, tzinfo=timezone.utc), quotes={}, cash=96_000)
        self.assertAlmostEqual(snapshot["daily_return"], -0.04, places=6)
        self.assertAlmostEqual(snapshot["drawdown"], 96_000 / 120_000 - 1, places=6)


if __name__ == "__main__":
    unittest.main()
