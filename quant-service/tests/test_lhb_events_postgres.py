"""Run the dragon-tiger readers against a real PostgreSQL schema.

Rows are written the way production writes them: a Fuyao-shaped list goes
through ``dragon_tiger_events`` and ``persist_market_events``.  Every test
works inside one transaction that is rolled back, and the sessions are dated
2099 so nothing collides with real lists in a shared development database.
"""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timezone


# Two stock rows of the 2026-09-17 list as Fuyao returned them.
GUOFANG = {"thscode": "601086.SH", "ticker": "601086", "name": "国芳集团",
           "concept_list": [{"name": "参股券商"}, {"name": "西部大开发"}], "change": 0.002059,
           "net_value": 12599059.02, "net_rate": 0.00763129, "hot_rank": 1, "buy_value": 112179750.0,
           "sell_value": 99580690.98, "range_days": 1, "hot_money_net_value": -2185780.98}
JINJIAN = {"thscode": "600127.SH", "ticker": "600127", "name": "金健米业",
           "concept_list": [{"name": "乳业"}, {"name": "玉米"}], "change": 0.099662,
           "net_value": 215363530.68, "net_rate": 0.09073764, "hot_rank": 2, "buy_value": 465896012.93,
           "sell_value": 250532482.25, "limit_reason": "粮油食品+粮食储备+健康食品", "range_days": 1,
           "hot_money_net_value": -17657808.15}


def _list(trade_date: str, *items: dict) -> dict:
    return {"timestamp": 4076553600000, "board_type": "all", "trade_date": trade_date, "count": len(items),
            "stock_count": len(items), "stock_items": list(items), "hot_money_items": []}


def _utc(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2099, 3, day, hour, minute, tzinfo=timezone.utc)


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class DragonTigerReadSqlTests(unittest.TestCase):
    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row
        self.connection = psycopg.connect(
            host=os.getenv("PGHOST"), port=os.getenv("PGPORT", "5432"), dbname=os.getenv("PGDATABASE", "n8n"),
            user=os.getenv("PGUSER", "n8n"), password=os.getenv("PGPASSWORD", ""), row_factory=dict_row,
        )
        connection = self.connection

        class Database:
            @contextmanager
            def transaction(self):
                yield connection

        self.database = Database()

    def tearDown(self) -> None:
        self.connection.rollback()
        self.connection.close()

    def _capture(self, data: dict, observed_at: datetime) -> None:
        """One run of the post-close archive job's write path."""
        from app.datasources.sources.fuyao_evidence import dragon_tiger_events
        from app.public_market_repository import persist_market_events
        persist_market_events(self.database, "fuyao_ths", dragon_tiger_events(data, observed_at))

    def test_a_row_captured_after_the_snapshot_is_not_visible(self) -> None:
        from app.strategy_context_read_model import lhb_context

        self._capture(_list("2099-03-02", GUOFANG), _utc(2, 10))
        self._capture(_list("2099-03-03", {**GUOFANG, "net_value": -1.0}), _utc(3, 10))

        def sessions(observed_at: datetime) -> list[str]:
            rows = lhb_context(self.database, ["601086.SH"], observed_at).get("601086.SH", [])
            return [row["trade_date"] for row in rows if str(row["trade_date"]).startswith("2099")]

        self.assertEqual(sessions(_utc(2, 9, 59)), [])
        self.assertEqual(sessions(_utc(3, 9, 59)), ["2099-03-02"])
        self.assertEqual(sessions(_utc(3, 10)), ["2099-03-03", "2099-03-02"])
        latest = lhb_context(self.database, ["601086.SH"], _utc(3, 10))["601086.SH"][0]
        self.assertEqual((latest["row"]["net_value"], latest["seat_detail"]), (-1.0, "unavailable"))

    def test_a_late_list_belongs_to_its_session_and_honours_the_checkpoint(self) -> None:
        from app.numeric_utils import intraday_number
        from app.post_close_evidence import lhb_context
        from app.post_close_evidence_repository import lhb_event_rows, load_lhb_context_rows

        # 03-02's list published late: the 03-03 evening run captured it
        # (17:30 Shanghai) before 03-03's own list (18:00).
        self._capture(_list("2099-03-02", GUOFANG, JINJIAN), _utc(3, 9, 30))
        self._capture(_list("2099-03-03", {**GUOFANG, "net_value": -1.0}), _utc(3, 10))

        session = lhb_context(load_lhb_context_rows(self.database, date(2099, 3, 2)), number=intraday_number)
        self.assertEqual(set(session), {"601086.SH", "600127.SH"})
        self.assertEqual(session["601086.SH"]["net_buy"], 12599059.02)   # not the next session's row
        self.assertEqual(session["600127.SH"]["limit_reason"], "粮油食品+粮食储备+健康食品")
        self.assertEqual(session["600127.SH"]["concepts"], ["乳业", "玉米"])
        self.assertIsNone(session["600127.SH"]["institution_net_buy"])
        self.assertEqual(lhb_event_rows(self.connection, date(2099, 3, 2), available_by=_utc(3, 9, 29)), [])
        self.assertEqual({row["symbol"] for row in lhb_event_rows(self.connection, date(2099, 3, 2), available_by=_utc(3, 9, 30))},
                         {"601086.SH", "600127.SH"})

    def test_a_body_that_is_not_json_is_skipped_not_fatal(self) -> None:
        from app.post_close_evidence_repository import lhb_event_rows

        self._capture(_list("2099-03-02", GUOFANG), _utc(2, 10))
        self.connection.execute(
            """INSERT INTO quant.market_events(event_id,symbol,event_type,occurred_at,available_at,source,title,body)
               VALUES(gen_random_uuid(),'601086.SH','lhb_ths',%s,%s,'fuyao_ths','龙虎榜（同花顺）','{"trade_date": NaN')""",
            (_utc(2, 11), _utc(2, 11)),
        )
        self.assertEqual([row["symbol"] for row in lhb_event_rows(self.connection, date(2099, 3, 2))], ["601086.SH"])

    def test_the_sector_rebuild_takes_the_list_by_its_own_trade_date(self) -> None:
        from app.sector_flow_repository import rebuild_sector_flow_daily_features
        from app.sector_membership_repository import persist_observed_snapshot

        day = date(2099, 3, 2)
        self.connection.execute("INSERT INTO quant.providers(provider_key,label) VALUES('test_sector','test') ON CONFLICT DO NOTHING")
        self.connection.execute(
            """INSERT INTO quant.sector_taxonomies(taxonomy_key,label,provider_key)
               VALUES('ths_concept_flow','test','test_sector') ON CONFLICT DO NOTHING""")
        self.connection.execute(
            """INSERT INTO quant.sectors(taxonomy_key,sector_key,label)
               VALUES('ths_concept_flow','TEST_LHB','test') ON CONFLICT DO NOTHING""")
        persist_observed_snapshot(self.connection, "ths_concept_flow", "TEST_LHB",
                                  [{"code": "601086.SH"}, {"code": "600127.SH"}], "test_sector", _utc(2, 1),
                                  member_symbol=lambda row: row["code"], ensure_instrument=lambda *_args: None)
        self.connection.execute(
            """INSERT INTO quant.sector_market_observations(
                   taxonomy_key,sector_key,trading_date,provider_key,available_at,change_pct,net_amount)
               VALUES('ths_concept_flow','TEST_LHB',%s,'test_sector',%s,1.5,1000000)""", (day, _utc(2, 8)))
        # The session's list is captured late, on the next evening; an
        # AKShare row for the same stock and day is outranked by it.
        self._capture(_list("2099-03-02", {**GUOFANG, "net_value": -5000000.0}, JINJIAN), _utc(3, 9, 30))
        self.connection.execute(
            """INSERT INTO quant.market_events(event_id,symbol,event_type,occurred_at,available_at,source,title,body)
               VALUES(gen_random_uuid(),'601086.SH','lhb_event',%s,%s,'akshare','龙虎榜',
                      '{"龙虎榜净买额": 9999, "龙虎榜成交额": 1}')""", (_utc(2, 9), _utc(2, 9)),
        )

        rebuild_sector_flow_daily_features(self.database, day, day)

        row = self.connection.execute(
            """SELECT lhb_stock_count,lhb_net_amount,lhb_negative_count FROM quant.sector_flow_daily_features
                WHERE taxonomy_key='ths_concept_flow' AND sector_key='TEST_LHB' AND trading_date=%s""", (day,),
        ).fetchone()
        self.assertEqual(row["lhb_stock_count"], 2)
        self.assertAlmostEqual(float(row["lhb_net_amount"]), -5000000.0 + 215363530.68, places=2)
        self.assertEqual(row["lhb_negative_count"], 1)


if __name__ == "__main__":
    unittest.main()
