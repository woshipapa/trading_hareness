"""Run this change set's new SQL against a real PostgreSQL schema.

Every test works inside one transaction that is rolled back, so it leaves no
rows behind in a shared development database.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch
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

    def test_the_close_records_its_st_names_as_dated_evidence(self) -> None:
        from app.longhu_market_repository import record_st_evidence

        observed_at = datetime(2099, 3, 2, 9, 0, tzinfo=timezone.utc)
        rows = [{"ts_code": "990001.SH", "name": "*ST测试"}, {"ts_code": "999999.SH", "name": "ST无记录"},
                {"ts_code": "990002.SH", "name": "测试二"}]
        self.assertEqual(record_st_evidence(self.connection, date(2099, 3, 2), rows, "test", observed_at), 2)
        stored = self.connection.execute(
            "SELECT symbol,is_st,status_date FROM quant.instrument_lifecycle_evidence WHERE provider='test'").fetchall()
        # A code with no instrument row is skipped rather than failing the batch.
        self.assertEqual([(row["symbol"], row["is_st"], row["status_date"]) for row in stored],
                         [("990001.SH", True, date(2099, 3, 2))])

    def test_a_failed_st_evidence_write_leaves_the_close_transaction_usable(self) -> None:
        from app import longhu_market_repository

        observed_at = datetime(2099, 3, 2, 9, 0, tzinfo=timezone.utc)
        with patch.object(longhu_market_repository, "ST_EVIDENCE_SQL", "SELECT no_such_column FROM quant.instruments"):
            self.assertEqual(longhu_market_repository.record_st_evidence(
                self.connection, date(2099, 3, 2), [{"ts_code": "990001.SH", "name": "*ST测试"}], "test", observed_at), 0)
        # The savepoint rolled back; the surrounding transaction still runs statements.
        self.assertEqual(self.connection.execute("SELECT 1 AS ok").fetchone()["ok"], 1)

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


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class T1SettlementSqlTests(unittest.TestCase):
    """All three settlement lines, on real tables, through the shared T+1 rule."""

    symbol = "990010.SH"

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row
        self.connection = psycopg.connect(
            host=os.getenv("PGHOST"), port=os.getenv("PGPORT", "5432"), dbname=os.getenv("PGDATABASE", "n8n"),
            user=os.getenv("PGUSER", "n8n"), password=os.getenv("PGPASSWORD", ""), row_factory=dict_row,
        )
        self.connection.execute("INSERT INTO quant.instruments(symbol,exchange) VALUES(%s,'SH') ON CONFLICT DO NOTHING",
                                (self.symbol,))
        # Signal day 2099-03-02, entry 03-03 at 10.00.  03-04 closes sealed at
        # limit-down 9.00, so a 1-session idea rolls to 03-05 (8.60).
        closes = [(date(2099, 3, 2), 10.0, 10.0, 10.0), (date(2099, 3, 3), 10.0, 10.0, 10.0),
                  (date(2099, 3, 4), 9.0, 9.0, 10.0), (date(2099, 3, 5), 8.8, 8.6, 9.0)]
        for day, open_, close, pre_close in closes:
            self.connection.execute(
                """INSERT INTO quant.canonical_bars_daily(symbol,trading_date,open,high,low,close,pre_close,adj_factor,
                       is_suspended,limit_up,limit_down,selected_provider,available_at)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,1,false,%s,%s,'test',now())""",
                (self.symbol, day, open_, max(open_, close), min(open_, close), close, pre_close,
                 round(pre_close * 1.1, 2), round(pre_close * 0.9, 2)),
            )

    def tearDown(self) -> None:
        self.connection.rollback()
        self.connection.close()

    def test_the_candidate_ledger_settles_under_t1_and_scores_outflow_as_bearish(self) -> None:
        from app.strategy_daily_candidate_ledger import HORIZON_DAYS, settle_ledger_outcomes
        from app.t1_settlement import SETTLEMENT_VERSION
        for key in ("board_stock_mining_inflow", "board_stock_mining_outflow"):
            self.connection.execute(
                """INSERT INTO quant.strategy_daily_candidates(strategy_key,as_of_date,symbol,source_table,score_scale)
                   VALUES(%s,'2099-03-02',%s,'test','unbounded_positive')""", (key, self.symbol))
        # Ten sessions are not yet observable: nothing settles, nothing is written.
        self.assertEqual(settle_ledger_outcomes(self.connection, date(2099, 3, 6)), 0)
        self.assertEqual(HORIZON_DAYS, 10)

    def test_recommendations_settle_through_the_shared_rule(self) -> None:
        from app.outcome_recomputation import recompute
        run_id = uuid.uuid4()
        self.connection.execute(
            """INSERT INTO quant.recommendation_runs(run_id,as_of_date,model_version,market_regime)
               VALUES(%s,'2099-03-02','test','neutral')""", (run_id,))
        self.connection.execute(
            """INSERT INTO quant.recommendations(run_id,rank,symbol,decision,score,score_breakdown,explanation,direction,horizon_days)
               VALUES(%s,1,%s,'research_candidate',1,'{}','["test"]',1,1)""", (run_id, self.symbol))

        connection = self.connection

        class Database:
            def transaction(self):
                class Context:
                    def __enter__(self): return connection
                    def __exit__(self, *_args): return False
                return Context()

        result = recompute(date(2099, 3, 6), cn_today=lambda: date(2099, 3, 6), db=Database(),
                           recompute_intraday_signal_outcomes=lambda _day: {"outcome_rows": 0})
        self.assertEqual(result["recommendation_outcomes"], 1)
        row = self.connection.execute(
            "SELECT * FROM quant.outcomes WHERE recommendation_run_id=%s", (run_id,)).fetchone()
        # One-day horizon: bought 03-03 open, could not sell into the 03-04
        # limit-down close, sold 03-05 at 8.60.
        self.assertEqual(row["entry_date"], date(2099, 3, 3))
        self.assertEqual(row["exit_date"], date(2099, 3, 5))
        self.assertEqual(row["tradability"], "exit_rolled_past_blocked_session")
        self.assertEqual(row["sessions_held"], 3)
        self.assertAlmostEqual(float(row["raw_return"]), 8.6 / 10.0 - 1, places=9)
        self.assertLess(float(row["net_return"]), float(row["raw_return"]))
        self.assertEqual(row["settlement_version"], "t1-settlement-v1")
        # A second run finds it current and does no work.
        again = recompute(date(2099, 3, 6), cn_today=lambda: date(2099, 3, 6), db=Database(),
                          recompute_intraday_signal_outcomes=lambda _day: {"outcome_rows": 0})
        self.assertEqual(again["recommendation_outcomes"], 0)

    def test_post_close_candidates_settle_through_the_shared_rule(self) -> None:
        from app.post_close_candidate_outcomes import CandidateOutcomeTarget, settle_candidate_outcomes
        run_id = uuid.uuid4()
        self.connection.execute(
            """INSERT INTO quant.post_close_strategy_runs(run_id,run_key,as_of_date,model_version,status)
               VALUES(%s,'t1-test','2099-03-02','test','completed')""", (run_id,))
        self.connection.execute(
            """INSERT INTO quant.post_close_strategy_candidates(run_id,rank,symbol,candidate_type,score)
               VALUES(%s,1,%s,'base_ready_30d',1)""", (run_id, self.symbol))
        target = CandidateOutcomeTarget("post_close_strategy_candidates", "post_close_strategy_runs",
                                        "post_close_strategy_candidate_outcomes", 2)
        self.assertEqual(settle_candidate_outcomes(self.connection, date(2099, 3, 6), target), 1)
        row = self.connection.execute(
            "SELECT * FROM quant.post_close_strategy_candidate_outcomes WHERE run_id=%s", (run_id,)).fetchone()
        self.assertEqual(row["exit_date"], date(2099, 3, 5))
        self.assertEqual(row["direction"], 1)
        self.assertEqual(settle_candidate_outcomes(self.connection, date(2099, 3, 6), target), 0)


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class ResearchTrialSqlTests(unittest.TestCase):
    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row
        self.connection = psycopg.connect(
            host=os.getenv("PGHOST"), port=os.getenv("PGPORT", "5432"), dbname=os.getenv("PGDATABASE", "n8n"),
            user=os.getenv("PGUSER", "n8n"), password=os.getenv("PGPASSWORD", ""), row_factory=dict_row,
        )

    def tearDown(self) -> None:
        self.connection.rollback()
        self.connection.close()

    def test_earlier_variants_keep_counting_and_reruns_do_not(self) -> None:
        import random
        from app.research_trial_repository import latest_trials, record_family
        generator = random.Random(3)
        series = lambda drift: [drift + generator.gauss(0, 0.01) for _ in range(60)]
        first = record_family(self.connection, family="test_family", return_basis="test", source="test", variants=[
            {"variant_key": "a", "parameters": {"x": 1}, "returns": series(0.002),
             "sample_start": date(2099, 1, 1), "sample_end": date(2099, 3, 1)},
            {"variant_key": "b", "parameters": {"x": 2}, "returns": series(0.0),
             "sample_start": date(2099, 1, 1), "sample_end": date(2099, 3, 1)},
        ])
        self.assertEqual({row["family_trials"] for row in first}, {2})
        # A later run evaluates a new variant alone and re-evaluates "a" on a
        # longer window: the family still counts a, b and c - three trials.
        second = record_family(self.connection, family="test_family", return_basis="test", source="test", variants=[
            {"variant_key": "c", "parameters": {"x": 3}, "returns": series(0.001),
             "sample_start": date(2099, 1, 1), "sample_end": date(2099, 3, 2)},
            {"variant_key": "a", "parameters": {"x": 1}, "returns": series(0.002),
             "sample_start": date(2099, 1, 1), "sample_end": date(2099, 3, 2)},
        ])
        self.assertEqual({row["family_trials"] for row in second}, {3})
        payload = latest_trials(self.connection, "test_family")
        self.assertEqual(sorted(item["variant_key"] for item in payload["items"]), ["a", "b", "c"])
        self.assertEqual(payload["live_effect"], "none")
        latest_a = next(item for item in payload["items"] if item["variant_key"] == "a")
        self.assertEqual(latest_a["sample_end"], date(2099, 3, 2))
        self.assertIsNotNone(latest_a["deflated_sharpe"])
        self.assertIsNotNone(latest_a["q_value"])

    def test_outcome_families_read_settled_rows(self) -> None:
        from app.research_trial_repository import evaluate_outcome_families
        # Runs cleanly against the real schema even with no settled rows.
        self.assertEqual(evaluate_outcome_families(self.connection, date(2099, 3, 6)),
                         {"candidate_ledger": 0, "xiaojie_leader_flow": 0})


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class PriorSessionSentimentSqlTests(unittest.TestCase):
    """The ten-day rule reads the last closed session's cycle as known then."""

    symbol = "990020.SH"

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row
        self.connection = psycopg.connect(
            host=os.getenv("PGHOST"), port=os.getenv("PGPORT", "5432"), dbname=os.getenv("PGDATABASE", "n8n"),
            user=os.getenv("PGUSER", "n8n"), password=os.getenv("PGPASSWORD", ""), row_factory=dict_row,
        )
        self.connection.execute("INSERT INTO quant.instruments(symbol,exchange) VALUES(%s,'SH') ON CONFLICT DO NOTHING",
                                (self.symbol,))
        # Sessions 2099-05-06 and 2099-05-07; each day's bars land after its close.
        for day in (date(2099, 5, 6), date(2099, 5, 7)):
            self.connection.execute(
                """INSERT INTO quant.canonical_bars_daily(symbol,trading_date,open,high,low,close,pre_close,adj_factor,
                       is_suspended,limit_up,limit_down,selected_provider,available_at)
                   VALUES(%s,%s,10,10,10,10,10,1,false,11,9,'test',%s)""",
                (self.symbol, day, datetime(day.year, day.month, day.day, 9, tzinfo=timezone.utc)),
            )

    def tearDown(self) -> None:
        self.connection.rollback()
        self.connection.close()

    def _reading(self, day: date, stage: str, calculated_at: datetime) -> None:
        self.connection.execute(
            """INSERT INTO quant.sentiment_cycle_daily(trading_date,model_version,stage,sealed_count,broken_count,
                   max_board_height,high_board_count,calculated_at)
               VALUES(%s,'test',%s,0,0,0,0,%s)""",
            (day, stage, calculated_at),
        )

    def test_the_last_closed_session_is_read_as_known_at_scan_time(self) -> None:
        from app.sentiment_cycle_daily import read_prior_session_sentiment_cycle
        self._reading(date(2099, 5, 6), "icepoint", datetime(2099, 5, 6, 12, tzinfo=timezone.utc))
        self._reading(date(2099, 5, 7), "fermenting", datetime(2099, 5, 7, 12, tzinfo=timezone.utc))
        # 10:00 Shanghai on 05-08: the prior session is 05-07.
        scan = datetime(2099, 5, 8, 2, tzinfo=timezone.utc)
        reading = read_prior_session_sentiment_cycle(self.connection, scan)
        self.assertEqual((reading["trading_date"], reading["stage"]), (date(2099, 5, 7), "fermenting"))
        # During 05-07's own session its reading is not yet known; 05-06 is.
        during = read_prior_session_sentiment_cycle(self.connection, datetime(2099, 5, 7, 2, tzinfo=timezone.utc))
        self.assertEqual((during["trading_date"], during["stage"]), (date(2099, 5, 6), "icepoint"))

    def test_a_missing_or_late_reading_is_not_replaced_by_an_older_one(self) -> None:
        from app.sentiment_cycle_daily import read_prior_session_sentiment_cycle
        self._reading(date(2099, 5, 6), "fermenting", datetime(2099, 5, 6, 12, tzinfo=timezone.utc))
        scan = datetime(2099, 5, 8, 2, tzinfo=timezone.utc)
        self.assertIsNone(read_prior_session_sentiment_cycle(self.connection, scan))
        # Written only after the scan: still unknown at scan time.
        self._reading(date(2099, 5, 7), "fermenting", datetime(2099, 5, 8, 3, tzinfo=timezone.utc))
        self.assertIsNone(read_prior_session_sentiment_cycle(self.connection, scan))


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class RegimeStrataSqlTests(unittest.TestCase):
    """Settled ledger outcomes read back next to their signal date's readings."""

    symbols = ("990031.SH", "990032.SH")

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row
        self.connection = psycopg.connect(
            host=os.getenv("PGHOST"), port=os.getenv("PGPORT", "5432"), dbname=os.getenv("PGDATABASE", "n8n"),
            user=os.getenv("PGUSER", "n8n"), password=os.getenv("PGPASSWORD", ""), row_factory=dict_row,
        )
        from app.t1_settlement import SETTLEMENT_VERSION
        for symbol in self.symbols:
            self.connection.execute("INSERT INTO quant.instruments(symbol,exchange) VALUES(%s,'SH') ON CONFLICT DO NOTHING",
                                    (symbol,))
        # Two signal sessions: 2099-06-01 trend_up/fermenting, 2099-06-02 unread.
        outcomes = [(date(2099, 6, 1), self.symbols[0], 0.03), (date(2099, 6, 1), self.symbols[1], 0.01),
                    (date(2099, 6, 2), self.symbols[0], -0.02)]
        for as_of_date, symbol, net in outcomes:
            self.connection.execute(
                """INSERT INTO quant.strategy_daily_candidates(strategy_key,as_of_date,symbol,source_table,score_scale)
                   VALUES('regime_strata_test',%s,%s,'test','rank')""", (as_of_date, symbol))
            self.connection.execute(
                """INSERT INTO quant.strategy_daily_candidate_outcomes(strategy_key,as_of_date,symbol,entry_date,horizon_days,
                       entry_price,exit_date,net_return,settlement_version)
                   VALUES('regime_strata_test',%s,%s,%s,10,10,%s,%s,%s)""",
                (as_of_date, symbol, as_of_date + timedelta(days=1), as_of_date + timedelta(days=12), net,
                 SETTLEMENT_VERSION))
        self.connection.execute(
            """INSERT INTO quant.market_regime_daily(trading_date,model_version,regime_label,index_count)
               VALUES('2099-06-01','test','trend_up',4)""")
        self.connection.execute(
            """INSERT INTO quant.sentiment_cycle_daily(trading_date,model_version,stage,sealed_count,broken_count,
                   max_board_height,high_board_count) VALUES('2099-06-01','test','fermenting',0,0,0,0)""")

    def tearDown(self) -> None:
        self.connection.rollback()
        self.connection.close()

    def test_sessions_are_stratified_by_their_signal_date_readings(self) -> None:
        from app.research_catalog_read_model import regime_strata

        connection = self.connection

        class Database:
            def transaction(self):
                class Context:
                    def __enter__(self): return connection
                    def __exit__(self, *_args): return False
                return Context()

        payload = regime_strata(Database(), date(2099, 7, 1), "regime_strata_test")
        [line] = payload["strategies"]
        self.assertEqual(line["all"]["sessions"], 2)
        self.assertAlmostEqual(line["all"]["mean_net_return"], (0.02 + -0.02) / 2)
        by_key = {(item["kind"], item["stratum"]): item for item in line["strata"]}
        self.assertAlmostEqual(by_key[("regime", "trend_up")]["mean_net_return"], 0.02)
        self.assertAlmostEqual(by_key[("sentiment_stage", "fermenting")]["mean_net_return"], 0.02)
        self.assertAlmostEqual(by_key[("regime", "unknown")]["mean_net_return"], -0.02)
        # Outcomes not settled by the as-of date are left out.
        early = regime_strata(Database(), date(2099, 6, 13), "regime_strata_test")
        self.assertEqual(early["strategies"][0]["all"]["sessions"], 1)
