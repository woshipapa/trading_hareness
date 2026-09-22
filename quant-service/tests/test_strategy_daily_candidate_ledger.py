"""Real-PostgreSQL coverage for the unified cross-strategy candidate ledger."""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.liquidity_screen import MINIMUM_MEDIAN_DAILY_AMOUNT
from app.main import DailyBar, db, upsert_bar
from app.strategy_daily_candidate_ledger import (
    materialize_leader_flow_candidates,
    materialize_ledger,
    materialize_limit_linkage_candidates,
    materialize_post_close_candidates,
    materialize_teacher_review_candidates,
    settle_ledger_outcomes,
)


def _seed_bars(connection, symbol: str, run_date: date, *, amount: Decimal = Decimal("50000000")) -> date:
    entry_date = run_date + timedelta(days=1)
    # The candidate's own discovery-date bar (and a short trailing window) must
    # exist too: liquidity is judged as of run_date, not the forward window.
    prices = {run_date - timedelta(days=1): Decimal("9.90"), run_date: Decimal("10.00"),
              entry_date: Decimal("10.00"), entry_date + timedelta(days=1): Decimal("10.20")}
    for offset in range(2, 10):
        prices[entry_date + timedelta(days=offset)] = Decimal("10.20") + Decimal(offset) * Decimal("0.03")
    for trading_date, close in prices.items():
        upsert_bar(connection, DailyBar(
            symbol=symbol, trading_date=trading_date, open=close, high=close * Decimal("1.01"), low=close * Decimal("0.99"),
            close=close, volume=Decimal("1000000"), amount=amount, adj_factor=Decimal("1.0"), is_suspended=False,
            source="p0-ledger-test", available_at=datetime.combine(trading_date, datetime.min.time(), tzinfo=timezone.utc),
        ))
    return entry_date


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class PostCloseLedgerMaterializationTests(unittest.TestCase):
    symbol_liquid = "999988.SZ"
    symbol_illiquid = "999987.SZ"
    run_date = date(2099, 1, 2)
    as_of_date = date(2099, 1, 20)

    def _cleanup(self) -> None:
        with db.transaction() as connection:
            for symbol in (self.symbol_liquid, self.symbol_illiquid):
                connection.execute("DELETE FROM quant.strategy_daily_candidate_outcomes WHERE symbol=%s", (symbol,))
                connection.execute("DELETE FROM quant.strategy_daily_candidates WHERE symbol=%s", (symbol,))
                connection.execute("DELETE FROM quant.post_close_strategy_candidates WHERE symbol=%s", (symbol,))
                connection.execute("DELETE FROM quant.canonical_bars_daily WHERE symbol=%s", (symbol,))
                connection.execute("DELETE FROM quant.market_bars_daily WHERE symbol=%s", (symbol,))
                connection.execute("DELETE FROM quant.raw_market_observations WHERE symbol=%s", (symbol,))
                connection.execute("DELETE FROM quant.instruments WHERE symbol=%s", (symbol,))
            connection.execute("DELETE FROM quant.post_close_strategy_runs WHERE model_version=%s", ("p0-ledger-test",))

    def test_materializes_with_correct_strategy_key_scale_and_liquidity_flag(self) -> None:
        self._cleanup()
        try:
            with db.transaction() as connection:
                _seed_bars(connection, self.symbol_liquid, self.run_date, amount=MINIMUM_MEDIAN_DAILY_AMOUNT * 2)
                _seed_bars(connection, self.symbol_illiquid, self.run_date, amount=Decimal("1000"))
                # upsert_bar has no list_date field; set it directly so the liquid
                # fixture actually clears the listing-age screen.
                connection.execute(
                    "UPDATE quant.instruments SET list_date=%s WHERE symbol=ANY(%s)",
                    (date(2000, 1, 1), [self.symbol_liquid, self.symbol_illiquid]),
                )
                run = connection.execute(
                    """INSERT INTO quant.post_close_strategy_runs(run_key,as_of_date,model_version,status)
                       VALUES('p0-ledger-test',%s,'p0-ledger-test','completed') RETURNING run_id""",
                    (self.run_date,),
                ).fetchone()
                connection.execute(
                    """INSERT INTO quant.post_close_strategy_candidates(run_id,rank,symbol,candidate_type,score)
                       VALUES(%s,1,%s,'base_ready_30d',90),(%s,2,%s,'fresh_start_15d',70)""",
                    (run["run_id"], self.symbol_liquid, run["run_id"], self.symbol_illiquid),
                )
            with db.transaction() as connection:
                stored = materialize_post_close_candidates(connection, self.run_date)
            self.assertGreaterEqual(stored, 2)
            with db.transaction() as connection:
                rows = {row["symbol"]: dict(row) for row in connection.execute(
                    "SELECT * FROM quant.strategy_daily_candidates WHERE symbol=ANY(%s)",
                    ([self.symbol_liquid, self.symbol_illiquid],),
                ).fetchall()}
            self.assertEqual(rows[self.symbol_liquid]["strategy_key"], "post_close_base_ready")
            self.assertEqual(rows[self.symbol_liquid]["score_scale"], "bounded_0_100")
            self.assertTrue(rows[self.symbol_liquid]["liquidity_eligible"])
            self.assertEqual(rows[self.symbol_illiquid]["strategy_key"], "post_close_fresh_start")
            self.assertFalse(rows[self.symbol_illiquid]["liquidity_eligible"])
            self.assertIn("median_amount_below_floor", rows[self.symbol_illiquid]["liquidity_flags"])
        finally:
            self._cleanup()

    def test_settlement_prices_the_ledger_entry_at_next_session_open(self) -> None:
        self._cleanup()
        try:
            with db.transaction() as connection:
                _seed_bars(connection, self.symbol_liquid, self.run_date, amount=MINIMUM_MEDIAN_DAILY_AMOUNT * 2)
                run = connection.execute(
                    """INSERT INTO quant.post_close_strategy_runs(run_key,as_of_date,model_version,status)
                       VALUES('p0-ledger-test',%s,'p0-ledger-test','completed') RETURNING run_id""",
                    (self.run_date,),
                ).fetchone()
                connection.execute(
                    """INSERT INTO quant.post_close_strategy_candidates(run_id,rank,symbol,candidate_type,score)
                       VALUES(%s,1,%s,'base_ready_30d',90)""",
                    (run["run_id"], self.symbol_liquid),
                )
            with db.transaction() as connection:
                materialize_post_close_candidates(connection, self.run_date)
            with db.transaction() as connection:
                settled = settle_ledger_outcomes(connection, self.as_of_date)
            self.assertGreaterEqual(settled, 1)
            with db.transaction() as connection:
                outcome = connection.execute(
                    "SELECT * FROM quant.strategy_daily_candidate_outcomes WHERE symbol=%s", (self.symbol_liquid,)
                ).fetchone()
            self.assertIsNotNone(outcome)
            self.assertEqual(outcome["strategy_key"], "post_close_base_ready")
            self.assertEqual(Decimal(outcome["entry_price"]), Decimal("10.00"))
            self.assertEqual(outcome["horizon_days"], 10)
        finally:
            self._cleanup()


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class LimitLinkageLedgerMaterializationTests(unittest.TestCase):
    symbol = "999986.SZ"
    run_date = date(2099, 1, 2)

    def _cleanup(self) -> None:
        with db.transaction() as connection:
            connection.execute("DELETE FROM quant.strategy_daily_candidates WHERE symbol=%s", (self.symbol,))
            connection.execute("DELETE FROM quant.intraday_limit_linkage_candidates WHERE symbol=%s", (self.symbol,))
            connection.execute("DELETE FROM quant.intraday_limit_linkage_mining_runs WHERE trade_date=%s", (self.run_date,))
            connection.execute("DELETE FROM quant.canonical_bars_daily WHERE symbol=%s", (self.symbol,))
            connection.execute("DELETE FROM quant.market_bars_daily WHERE symbol=%s", (self.symbol,))
            connection.execute("DELETE FROM quant.raw_market_observations WHERE symbol=%s", (self.symbol,))
            connection.execute("DELETE FROM quant.instruments WHERE symbol=%s", (self.symbol,))

    def test_only_the_latest_intraday_run_of_the_day_is_materialized(self) -> None:
        self._cleanup()
        try:
            with db.transaction() as connection:
                _seed_bars(connection, self.symbol, self.run_date)
                earlier = connection.execute(
                    """INSERT INTO quant.intraday_limit_linkage_mining_runs(observed_at,trade_date,status)
                       VALUES(%s,%s,'completed') RETURNING linkage_run_id""",
                    (datetime(2099, 1, 2, 2, 0, tzinfo=timezone.utc), self.run_date),
                ).fetchone()
                later = connection.execute(
                    """INSERT INTO quant.intraday_limit_linkage_mining_runs(observed_at,trade_date,status)
                       VALUES(%s,%s,'completed') RETURNING linkage_run_id""",
                    (datetime(2099, 1, 2, 6, 0, tzinfo=timezone.utc), self.run_date),
                ).fetchone()
                connection.execute(
                    """INSERT INTO quant.intraday_limit_linkage_candidates(linkage_run_id,rank,symbol,score,shared_concepts)
                       VALUES(%s,1,%s,40,1)""",
                    (earlier["linkage_run_id"], self.symbol),
                )
                connection.execute(
                    """INSERT INTO quant.intraday_limit_linkage_candidates(linkage_run_id,rank,symbol,score,shared_concepts)
                       VALUES(%s,1,%s,88,3)""",
                    (later["linkage_run_id"], self.symbol),
                )
            with db.transaction() as connection:
                stored = materialize_limit_linkage_candidates(connection, self.run_date)
            self.assertEqual(stored, 1)
            with db.transaction() as connection:
                row = connection.execute(
                    "SELECT raw_score,source_run_id FROM quant.strategy_daily_candidates WHERE symbol=%s", (self.symbol,)
                ).fetchone()
            self.assertEqual(Decimal(row["raw_score"]), Decimal("88"))
            self.assertEqual(str(row["source_run_id"]), str(later["linkage_run_id"]))
        finally:
            self._cleanup()


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class LedgerOrchestratorSmokeTests(unittest.TestCase):
    def test_materialize_ledger_runs_every_source_without_error(self) -> None:
        with db.transaction() as connection:
            result = materialize_ledger(connection, date(2099, 1, 20))
        self.assertEqual(set(result), {
            "materialize_post_close_candidates", "materialize_pattern_candidates", "materialize_ten_day_leader_candidates",
            "materialize_limit_linkage_candidates", "materialize_board_stock_mining_candidates",
            "materialize_recommendation_candidates", "materialize_teacher_review_candidates",
            "materialize_leader_flow_candidates",
        })
        self.assertTrue(all(isinstance(value, int) for value in result.values()))



@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class TeacherAndLeaderFlowLedgerTests(unittest.TestCase):
    relay_symbol, record_symbol = "999986.SZ", "999985.SZ"
    run_date = date(2099, 2, 3)

    def _cleanup(self) -> None:
        with db.transaction() as connection:
            for symbol in (self.relay_symbol, self.record_symbol):
                connection.execute("DELETE FROM quant.strategy_daily_candidates WHERE symbol=%s", (symbol,))
                connection.execute("DELETE FROM quant.xiaojie_leader_flow_observations WHERE symbol=%s", (symbol,))
                connection.execute("DELETE FROM quant.canonical_bars_daily WHERE symbol=%s", (symbol,))
                connection.execute("DELETE FROM quant.market_bars_daily WHERE symbol=%s", (symbol,))
            connection.execute("""DELETE FROM quant.raw_market_observations WHERE provider_key='teacher_review'
                                   AND capability='teacher_review_pack' AND payload->'pack'->>'review_date'=%s""",
                               (self.run_date.isoformat(),))
            for symbol in (self.relay_symbol, self.record_symbol):
                connection.execute("DELETE FROM quant.instruments WHERE symbol=%s", (symbol,))

    def _pack(self, connection, pack_id: str, stocks: list[dict], supersedes: list[str] | None = None) -> None:
        from psycopg.types.json import Json
        body = {"pack": {"pack_id": pack_id, "review_date": self.run_date.isoformat(), "supersedes": supersedes or [],
                         "analyst": {"analyst_id": "test-analyst"}, "stocks": stocks}}
        connection.execute(
            """INSERT INTO quant.raw_market_observations(provider_key,capability,market,symbol,effective_at,available_at,payload_sha256,normalized,payload)
               VALUES('teacher_review','teacher_review_pack','cn','analyst:test-analyst',%s,%s,%s,%s,%s)""",
            (datetime(2099, 2, 3, 7, 0, tzinfo=timezone.utc), datetime(2099, 2, 3, 12, 0, tzinfo=timezone.utc),
             pack_id.ljust(64, "0"), Json(body), Json(body)))

    def test_teacher_plans_enter_the_ledger_by_kind_and_superseded_packs_do_not(self) -> None:
        self._cleanup()
        try:
            with db.transaction() as connection:
                _seed_bars(connection, self.relay_symbol, self.run_date)
                _seed_bars(connection, self.record_symbol, self.run_date)
                self._pack(connection, "old", [{"code": "999986", "playbook": "prior_high_breakout", "stance": "watch"}])
                self._pack(connection, "new", [
                    {"code": "999986", "ts_code": self.relay_symbol, "playbook": "relay_race", "stance": "watch", "group": "g1"},
                    {"code": "999985", "ts_code": self.record_symbol, "playbook": "rejected", "stance": "avoid"},
                ], supersedes=["old"])
            with db.transaction() as connection:
                stored = materialize_teacher_review_candidates(connection, self.run_date)
                rows = connection.execute(
                    """SELECT strategy_key,symbol,score_scale,evidence FROM quant.strategy_daily_candidates
                        WHERE as_of_date=%s AND strategy_key LIKE 'teacher_review%%' AND symbol=ANY(%s)""",
                    (self.run_date, [self.relay_symbol, self.record_symbol])).fetchall()
            self.assertEqual(stored, 1)
            self.assertEqual([(row["strategy_key"], row["symbol"]) for row in rows], [("teacher_review_relay", self.relay_symbol)])
            self.assertEqual(rows[0]["evidence"]["pack_id"], "new")
            self.assertEqual(rows[0]["score_scale"], "unscored_plan")
        finally:
            self._cleanup()

    def test_leader_flow_modes_collapse_to_one_row_per_stock_and_radar_is_separate(self) -> None:
        self._cleanup()
        try:
            seen = datetime(2099, 2, 3, 2, 0, tzinfo=timezone.utc)
            with db.transaction() as connection:
                _seed_bars(connection, self.relay_symbol, self.run_date)
                for mode, count, decision in (("leader_pullback", 3, "research_candidate"),
                                              ("right_side_breakout", 2, "research_candidate"),
                                              ("launch_radar", 5, "launch_watch"),
                                              ("潜龙出海_swing_gated", 7, "no_trade")):
                    connection.execute(
                        """INSERT INTO quant.xiaojie_leader_flow_observations(trading_date,symbol,mode,model_version,first_seen_at,
                               last_seen_at,observation_count,decision) VALUES(%s,%s,%s,'test',%s,%s,%s,%s)""",
                        (self.run_date, self.relay_symbol, mode, seen, seen, count, decision))
            with db.transaction() as connection:
                stored = materialize_leader_flow_candidates(connection, self.run_date)
                rows = {row["strategy_key"]: row for row in connection.execute(
                    """SELECT strategy_key,raw_score,evidence FROM quant.strategy_daily_candidates
                        WHERE as_of_date=%s AND symbol=%s""", (self.run_date, self.relay_symbol)).fetchall()}
            self.assertEqual(stored, 2)
            self.assertEqual(rows["xiaojie_leader_flow"]["evidence"]["modes"], ["leader_pullback", "right_side_breakout"])
            self.assertEqual(rows["xiaojie_leader_flow"]["raw_score"], 5)   # the gated shadow row is not a detection
            self.assertEqual(rows["launch_radar"]["raw_score"], 5)
        finally:
            self._cleanup()


if __name__ == "__main__":
    unittest.main()
