"""Coverage for per-stock end-of-day capital flow ingestion."""

from __future__ import annotations

import asyncio
import os
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timezone
from types import SimpleNamespace

from app.main import db
from app.stock_money_flow_sync import FLOW_SOURCE, MINIMUM_COVERAGE_RATIO, persist_flow_rows, sync


class _Database:
    def __init__(self, stored):
        self.stored = stored
        self.queries = []

    @contextmanager
    def transaction(self):
        database = self

        class Connection:
            def execute(self, sql, params):
                database.queries.append((sql, params))
                return SimpleNamespace(fetchone=lambda: {"n": database.stored})

        yield Connection()


def run_sync(*, expected, stored):
    async def run(action, *args, **_kwargs):
        return action(*args)

    database = _Database(stored)
    result = asyncio.run(sync(date(2026, 10, 9), expected_symbols=lambda _day: expected,
                              run_database_blocking=run, db=database))
    return result, database


class FlowCoverageTests(unittest.TestCase):
    def test_a_close_with_its_flow_cross_section_is_completed(self):
        result, database = run_sync(expected=5300, stored=5290)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["rows"], {FLOW_SOURCE: 5290})
        self.assertEqual(database.queries[0][1], (date(2026, 10, 9), FLOW_SOURCE))
        self.assertIn("end_of_day_only", result["boundary"])

    def test_a_short_cross_section_is_reported_missing_with_its_count(self):
        result, _database = run_sync(expected=5300, stored=int(5300 * MINIMUM_COVERAGE_RATIO) - 1)
        self.assertEqual(result["status"], "missing")
        self.assertIn("of 5300 symbols", result["reason"])

    def test_no_daily_universe_blocks_before_reading_flow(self):
        result, database = run_sync(expected=0, stored=0)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(database.queries, [])


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class StockMoneyFlowPersistenceTests(unittest.TestCase):
    symbol = "999978.SZ"
    trade_date = date(2099, 4, 1)

    def _cleanup(self) -> None:
        with db.transaction() as connection:
            connection.execute("DELETE FROM quant.stock_money_flow_daily WHERE symbol=%s", (self.symbol,))
            connection.execute("DELETE FROM quant.instruments WHERE symbol=%s", (self.symbol,))

    def setUp(self) -> None:
        self._cleanup()
        self.addCleanup(self._cleanup)
        with db.transaction() as connection:
            connection.execute(
                "INSERT INTO quant.instruments(symbol,exchange) VALUES(%s,'SZ') ON CONFLICT DO NOTHING",
                (self.symbol,),
            )

    def test_three_vendors_coexist_and_reingest_updates_in_place(self) -> None:
        stamp = datetime(2099, 4, 1, tzinfo=timezone.utc)
        rows = [{"symbol": self.symbol, "trading_date": self.trade_date, "source": source,
                 "net_amount": value, "net_amount_rate": None, "buy_elg_amount": None,
                 "buy_lg_amount": None, "buy_md_amount": None, "buy_sm_amount": None, "raw": {}}
                for source, value in (("moneyflow", 1.0), ("moneyflow_dc", 2.0), ("moneyflow_ths", 3.0))]
        with db.transaction() as connection:
            persist_flow_rows(connection, rows, "tushare_test", stamp)
            stored = connection.execute(
                "SELECT source,net_amount FROM quant.stock_money_flow_daily WHERE symbol=%s ORDER BY source",
                (self.symbol,),
            ).fetchall()
        self.assertEqual([(r["source"], float(r["net_amount"])) for r in stored],
                         [("moneyflow", 1.0), ("moneyflow_dc", 2.0), ("moneyflow_ths", 3.0)])
        rows[0]["net_amount"] = 9.0
        with db.transaction() as connection:
            persist_flow_rows(connection, rows, "tushare_test", stamp)
            again = connection.execute(
                "SELECT count(*) n, max(net_amount) FILTER (WHERE source='moneyflow') v "
                "FROM quant.stock_money_flow_daily WHERE symbol=%s", (self.symbol,),
            ).fetchone()
        self.assertEqual(again["n"], 3)
        self.assertEqual(float(again["v"]), 9.0)

    def test_an_unknown_symbol_is_skipped_rather_than_violating_the_instrument_key(self) -> None:
        with db.transaction() as connection:
            persist_flow_rows(connection, [{
                "symbol": "999666.SZ", "trading_date": self.trade_date, "source": "moneyflow",
                "net_amount": 1.0, "net_amount_rate": None, "buy_elg_amount": None,
                "buy_lg_amount": None, "buy_md_amount": None, "buy_sm_amount": None, "raw": {},
            }], "tushare_test", datetime(2099, 4, 1, tzinfo=timezone.utc))
            count = connection.execute(
                "SELECT count(*) n FROM quant.stock_money_flow_daily WHERE symbol='999666.SZ'").fetchone()
        self.assertEqual(count["n"], 0)


if __name__ == "__main__":
    unittest.main()
