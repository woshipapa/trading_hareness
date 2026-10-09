"""Run the strategy cards, limit detail and radar reads against a real PostgreSQL schema.

The unit tests answer the SQL with a fake. This one executes every statement
the cards issue: ledger, per-symbol quote probes, limits, signals, point-in-
time concepts, events, the limit review, the leaderboard, the radar gate
and the regime. Each runs inside one transaction that is rolled back, and the
sessions are dated 2099 so nothing collides with real rows.
"""

from __future__ import annotations

import os
import unittest
from datetime import date

SESSION, AS_OF, EARLIER = date(2099, 3, 4), date(2099, 3, 3), date(2099, 3, 2)


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class StrategyCardsSqlTests(unittest.TestCase):
    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row
        self.connection = psycopg.connect(
            host=os.getenv("PGHOST"), port=os.getenv("PGPORT", "5432"), dbname=os.getenv("PGDATABASE", "n8n"),
            user=os.getenv("PGUSER", "n8n"), password=os.getenv("PGPASSWORD", ""), row_factory=dict_row,
        )
        self.connection.execute(
            """INSERT INTO quant.instruments(symbol,exchange,name) VALUES
                 ('699901.SH','SSE','卡片甲'),('699902.SH','SSE','卡片乙') ON CONFLICT(symbol) DO NOTHING""")
        for day, symbol, line in ((AS_OF, "699901.SH", "launch_radar"), (AS_OF, "699902.SH", "launch_radar"),
                                  (AS_OF, "699901.SH", "post_close_base_ready"),
                                  (EARLIER, "699902.SH", "launch_radar")):
            self.connection.execute(
                """INSERT INTO quant.strategy_daily_candidates(strategy_key,as_of_date,symbol,source_table,rank,
                                                                raw_score,score_scale)
                   VALUES(%s,%s,%s,'test',1,1,'0-100')""", (line, day, symbol))

    def tearDown(self) -> None:
        self.connection.rollback()
        self.connection.close()

    def test_every_card_statement_runs_on_the_real_schema(self) -> None:
        from app.strategy_cards_read_model import strategy_cards

        day = strategy_cards(self.connection, SESSION, per_line=5)
        self.assertEqual(day["picks_as_of"], AS_OF.isoformat())
        cards = {card["strategy_key"]: card for card in day["cards"]}
        self.assertEqual(sorted(cards), ["launch_radar", "post_close_base_ready"])
        pick = next(pick for pick in cards["launch_radar"]["picks"] if pick["symbol"] == "699901.SH")
        self.assertEqual((pick["name"], pick["resonance"], pick["price"]), ("卡片甲", 2, None))
        self.assertEqual(day["direction_gate"]["label"], "unknown")
        self.assertEqual(cards["launch_radar"]["leaderboard"]["5"]["without_bar"], 1)

    def test_every_indicator_health_statement_runs_on_the_real_schema(self) -> None:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from app.indicator_health import indicator_health

        day = indicator_health(self.connection, SESSION, datetime(2099, 3, 4, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
        statuses = {item["key"]: item["status"] for item in day["indicators"]}
        self.assertEqual(statuses["market.radar"], "missing")
        self.assertEqual(statuses["strategy.ledger"], "fail", "two lines on the previous session, below the warning floor of four")

    def test_the_research_boards_run_on_the_real_schema(self) -> None:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from app.research_boards import datasource_board, strategy_board

        now = datetime(2099, 3, 4, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertTrue(datasource_board(self.connection, now)["sources"])
        board = strategy_board(self.connection, now)
        self.assertTrue(board["strategies"])

    def test_minute_documents_round_trip_on_the_real_schema(self) -> None:
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        from app import minute_cross_section as mcs

        connection = self.connection

        class Database:
            def transaction(self):
                class Context:
                    def __enter__(self):
                        return connection

                    def __exit__(self, *exc):
                        return False
                return Context()

        minute = datetime(2099, 3, 4, 9, 25, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        rows = [{"symbol": "699901.SH", "ts_code": "699901.SH", "price": 10.5, "pct_change": 5.0, "turnover": 1e6,
                 "volume": 1e4, "raw": {"open_price": 10.0, "prev_price": 10.0}},
                {"symbol": "699902.SH", "ts_code": "699902.SH", "price": 9.5, "pct_change": -5.0, "turnover": 2e6,
                 "volume": 2e4, "raw": {"open_price": 9.8, "prev_price": 10.0}}]
        mcs.persist_document(Database(), minute, rows, {"pages": 2})
        mcs.persist_document(Database(), minute + timedelta(minutes=30), rows[:1], {"pages": 2})
        latest = mcs.latest(connection, SESSION)
        self.assertEqual(latest[0], minute + timedelta(minutes=30))
        first = mcs.first_between(connection, minute - timedelta(minutes=1), minute + timedelta(minutes=5))
        self.assertEqual(mcs.rows_for(first[1], ["699902.SH"])["699902.SH"], rows[1])
        self.assertEqual(len(mcs.day_documents(connection, SESSION)), 2)

    def test_the_backfill_converts_verifies_deletes_and_replays_on_the_real_schema(self) -> None:
        import json
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        from app import minute_cross_section_backfill as backfill

        connection = self.connection

        class Database:
            def transaction(self):
                class Context:
                    def __enter__(self):
                        return connection

                    def __exit__(self, *exc):
                        return False
                return Context()

        first = datetime(2099, 3, 4, 9, 31, tzinfo=ZoneInfo("Asia/Shanghai"))
        for offset in range(2):
            minute = first + timedelta(minutes=offset)
            for index, symbol in enumerate(("699901.SH", "699902.SH")):
                row = {"symbol": symbol, "ts_code": symbol, "price": 10.0 + offset, "pct_change": 2.5 * (index + 1),
                       "turnover": 1e6 * (offset + 1), "provider_key": "fuyao_ths",
                       "capability": "a_share_prices_snapshot", "record_index": index}
                connection.execute(
                    """INSERT INTO quant.raw_market_observations(provider_key,capability,market,symbol,effective_at,
                                                                  available_at,payload_sha256,normalized,payload)
                       VALUES('fuyao_ths','a_share_prices_snapshot','cn',%s,%s,%s,%s,%s::jsonb,%s::jsonb)""",
                    (symbol, minute, minute, f"test-{offset}-{index}", json.dumps(row), json.dumps(row)))
        database = Database()
        self.assertEqual(backfill.convert_day(database, SESSION, apply=True)["written"], 2)
        self.assertTrue(backfill.verify_day(database, SESSION)["ok"])
        replay = backfill.replay_radar(database, SESSION, apply=True)
        self.assertEqual((replay["minutes"], replay["stored"]), (2, 2))
        self.assertEqual(backfill.delete_day(database, SESSION, apply=True, pause=lambda _s: None)["deleted"], 4)
        self.assertEqual(backfill.legacy_minutes(connection, SESSION), [])

    def test_the_limit_detail_and_radar_reads_run_on_the_real_schema(self) -> None:
        from app.limit_detail_read_model import limit_detail_day
        from app.market_radar_runtime import radar_day

        self.assertEqual(limit_detail_day(self.connection, SESSION)["status"], "missing")
        self.assertEqual(radar_day(self.connection, SESSION)["points"], [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
