"""Real SQL acceptance on an explicitly supplied disposable database only."""
import os
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timezone

import psycopg
from app.daily_valuation_repository import project_valuations
from app.owner_daily_control_repository import read_persisted_control_rows
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

DAY = date(2026, 10, 9)
NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
DSN = os.getenv("VALUATION_TEST_DSN", "")
if not DSN and os.getenv("PGDATABASE", "").startswith(("quant_test_", "codex_valuation_test_")):
    DSN = make_conninfo(**{name: os.environ[env] for name, env in (
        ("host","PGHOST"),("port","PGPORT"),("dbname","PGDATABASE"),("user","PGUSER"),("password","PGPASSWORD"))
        if env in os.environ})


class TestDatabase:
    @contextmanager
    def transaction(self):
        with psycopg.connect(DSN, row_factory=dict_row) as connection:
            yield connection


@unittest.skipUnless(DSN, "requires a disposable database in VALUATION_TEST_DSN")
class ValuationPostgresTests(unittest.TestCase):
    def setUp(self):
        with psycopg.connect(DSN) as c:
            name = c.execute("SELECT current_database()").fetchone()[0]
            if not name.startswith(("codex_valuation_test_", "quant_test_")):
                self.fail("refusing a non-disposable database")
            c.execute("DELETE FROM quant.daily_fundamentals WHERE trading_date=%s", (DAY,))
            c.execute("DELETE FROM quant.raw_market_observations WHERE capability='a_share_valuations_snapshot'")
            c.execute("DELETE FROM quant.canonical_bars_daily WHERE trading_date=%s", (DAY,))
            c.execute("DELETE FROM quant.universe_membership_history WHERE universe_key='all_a'")
            for symbol in ("920001.BJ", "920002.BJ", "600000.SH"):
                c.execute("INSERT INTO quant.instruments(symbol,name,exchange) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                          (symbol,symbol,symbol.split('.')[1]))
                c.execute("INSERT INTO quant.universe_membership_history(universe_key,symbol,effective_from,source,priority) VALUES('all_a',%s,%s,'fixture',1)", (symbol,DAY))
                c.execute("INSERT INTO quant.canonical_bars_daily(symbol,trading_date,close,selected_provider,quality_status,available_at) VALUES(%s,%s,10,'fixture','fresh',%s)", (symbol,DAY,NOW))
            c.execute("INSERT INTO quant.daily_fundamentals(symbol,trading_date,pe,pb,turnover_rate,provider,available_at) VALUES('600000.SH',%s,10,1,2,'fixture',%s)", (DAY,NOW))

    def raw(self, symbol="920001.BJ", **fields):
        normalized = {"ts_code": symbol, "pe_ttm": -41.04, "pb_mrq": 2.45, **fields}
        with psycopg.connect(DSN) as c:
            c.execute("""INSERT INTO quant.raw_market_observations(provider_key,capability,market,symbol,effective_at,available_at,payload_sha256,normalized,payload)
                VALUES('fuyao_ths','a_share_valuations_snapshot','cn',%s,%s,%s,%s,%s,'{}')""",
                (symbol,NOW,NOW,symbol,Jsonb(normalized)))

    def test_preview_writes_nothing_and_apply_is_idempotent_and_preserves_provider(self):
        self.raw()
        self.raw("920002.BJ")
        self.raw("600000.SH")
        preview = project_valuations(TestDatabase(), DAY, projected_at=NOW)
        self.assertEqual(preview["candidate_symbols"], 2)
        self.assertEqual(preview["coverage_after"]["total"]["record_symbols"], 1)
        result = project_valuations(TestDatabase(), DAY, apply=True, projected_at=NOW)
        self.assertEqual(result["inserted"], 2)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage_after"]["total"]["turnover_rate_symbols"], 1)
        with TestDatabase().transaction() as c:
            combined = read_persisted_control_rows(c, "daily_basic", DAY)
        self.assertEqual(len(combined["rows"]), 3)
        self.assertEqual(combined["providers"], ["fixture", "fuyao_ths"])
        self.assertEqual(combined["field_symbols"]["turnover_rate"], 1)
        repeat = project_valuations(TestDatabase(), DAY, apply=True, projected_at=NOW)
        self.assertEqual(repeat["inserted"], 0)
        with psycopg.connect(DSN, row_factory=dict_row) as c:
            row = c.execute("SELECT * FROM quant.daily_fundamentals WHERE symbol='920001.BJ' AND trading_date=%s", (DAY,)).fetchone()
            self.assertEqual(float(row["pe"]), -41.04)
            self.assertIsNone(row["turnover_rate"])
            self.assertEqual(row["raw"]["field_basis"]["pe"], "pe_ttm")
            self.assertEqual(c.execute("SELECT count(*) AS n FROM quant.daily_fundamentals WHERE symbol='600000.SH'").fetchone()["n"], 1)

    def test_late_repair_changes_current_readiness_only(self):
        self.raw()
        weekend = datetime(2026, 10, 10, 2, tzinfo=timezone.utc)
        result = project_valuations(TestDatabase(), DAY, apply=True, projected_at=weekend)
        self.assertEqual(result["coverage_after"]["total"]["record_symbols"], 2)
        self.assertEqual(result["coverage_after"]["total"]["original_session_record_symbols"], 1)
        with psycopg.connect(DSN) as c:
            self.assertEqual(c.execute("SELECT available_at FROM quant.daily_fundamentals WHERE symbol='920001.BJ'").fetchone()[0], weekend)

    def test_all_null_and_missing_raw_keep_readiness_partial(self):
        self.raw(pe_ttm=None,pb_mrq=None)
        result = project_valuations(TestDatabase(), DAY, apply=True, projected_at=NOW)
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["rejected_evidence"], 1)

    def test_mixed_source_reader_keeps_the_original_session_cutoff(self):
        self.raw()
        project_valuations(TestDatabase(), DAY, apply=True,
            projected_at=datetime(2026, 10, 10, 2, tzinfo=timezone.utc))
        with TestDatabase().transaction() as c:
            rows = read_persisted_control_rows(c, "daily_basic", DAY)["rows"]
        self.assertEqual([row["ts_code"] for row in rows], ["600000.SH"])
        with TestDatabase().transaction() as c:
            repaired = read_persisted_control_rows(c,"daily_basic",DAY,
                as_of=datetime(2026, 10, 10, 2, tzinfo=timezone.utc))
        self.assertEqual(len(repaired["rows"]), 2)
        self.assertEqual(repaired["evidence_time_basis"], "repair_as_of")

    def test_two_projection_writers_cannot_acquire_the_same_transaction_lock(self):
        with psycopg.connect(DSN) as lock:
            lock.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"daily-valuation-projection-v1:{DAY}",))
            result = project_valuations(TestDatabase(), DAY, apply=True, projected_at=NOW)
            self.assertEqual(result["status"], "blocked")

    def test_each_statement_is_timed_and_the_transaction_budget_is_enforced_between_them(self):
        from app.daily_valuation_repository import StatementBudget
        self.raw()
        preview = project_valuations(TestDatabase(), DAY, projected_at=NOW)
        self.assertEqual((set(preview["timings_seconds"]), preview["budget_seconds"]), ({"coverage_before", "evidence"}, 60.0))
        ticks = iter([0.0, 1.0, 1.0, 2.0, 100.0])   # the first statement runs; the clock then passes the budget
        with self.assertRaisesRegex(TimeoutError, "60s budget before evidence"):
            project_valuations(TestDatabase(), DAY, apply=True, projected_at=NOW,
                               budget=StatementBudget(60.0, clock=lambda: next(ticks)))
        with psycopg.connect(DSN) as c:
            written = c.execute("SELECT count(*) FROM quant.daily_fundamentals WHERE trading_date=%s AND provider='fuyao_ths'",
                                (DAY,)).fetchone()[0]
        self.assertEqual(written, 0, "the transaction rolled back")

    def test_a_statement_over_its_cap_is_cancelled_and_named(self):
        from unittest import mock
        import app.daily_valuation_repository as repository
        self.raw()
        with mock.patch.dict(repository.STATEMENT_CAPS, {"evidence": 0.2}), \
                mock.patch.object(repository, "SOURCE_SQL", "SELECT pg_sleep(2), %(day)s::date AS day"):
            with self.assertRaisesRegex(TimeoutError, r"evidence exceeded its 0\.2s statement limit"):
                project_valuations(TestDatabase(), DAY, projected_at=NOW)

    def test_multi_source_selection_prefers_complete_existing_row_and_keeps_one_row_per_stock(self):
        with psycopg.connect(DSN) as c:
            c.execute("""INSERT INTO quant.daily_fundamentals(symbol,trading_date,pe,pb,provider,available_at)
                VALUES('600000.SH',%s,20,2,'fuyao_ths',%s)""", (DAY,NOW))
        with TestDatabase().transaction() as c:
            result = read_persisted_control_rows(c,"daily_basic",DAY)
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["source_provider"], "fixture")
        self.assertEqual(float(result["rows"][0]["pe"]), 10)


if __name__ == "__main__":
    unittest.main()
