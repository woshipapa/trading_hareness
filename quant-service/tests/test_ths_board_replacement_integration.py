"""Real-PostgreSQL coverage for the THS board replacements (Tushare retired, decision 0005).

The membership refresh, the board flows and the concept candidates are new
SQL against the existing schema, so each runs here against the migrated test
database inside one transaction that is rolled back.  Dates are in 2099 and
symbols 9999xx so nothing collides with other suites.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import unittest
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from psycopg.types.json import Json

CN = ZoneInfo("Asia/Shanghai")
DAY_ONE, DAY_TWO = date(2099, 3, 2), date(2099, 3, 3)
CONCEPT = "fuyao_ths_concept"


class _Rollback(Exception):
    pass


class _Within:
    """Hands every repository call the test's connection, so all of it rolls back."""

    def __init__(self, connection):
        self._connection = connection

    @contextlib.contextmanager
    def transaction(self):
        yield self._connection


def _at(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=CN).astimezone(timezone.utc)


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class ThsBoardReplacementIntegrationTests(unittest.TestCase):
    def run_rolled_back(self, body):
        from app.main import db

        try:
            with db.transaction() as connection:
                body(_Within(connection), connection)
                raise _Rollback
        except _Rollback:
            pass

    def test_a_daily_refresh_records_only_membership_changes(self):
        from app.fuyao_ths_membership_repository import (
            boards_due, listed_counts, persist_catalog, persist_member_snapshot, record_member_failure, refresh_progress,
        )
        from app.sector_membership_repository import point_in_time_membership_predicate

        def members(*symbols):
            return {symbol: {"name": f"测{symbol[:6]}", "thscode": symbol, "index_code": "885431.TI"} for symbol in symbols}

        def body(database, connection):
            persist_catalog(database, CONCEPT, "同花顺概念（Fuyao）", "cn_concept",
                            [("885431.TI", "新能源汽车"), ("885566.TI", "大飞机")], DAY_ONE)
            self.assertEqual(listed_counts(database, [CONCEPT, "fuyao_ths_industry"], DAY_ONE), {CONCEPT: 2})
            due, listed = boards_due(database, CONCEPT, DAY_ONE, 10)
            self.assertEqual(([row["sector_key"] for row in due], listed), (["885431.TI", "885566.TI"], 2))

            first = persist_member_snapshot(database, CONCEPT, "885431.TI", members("999901.SZ", "999902.SZ"),
                                            _at(DAY_ONE, 15, 40))
            self.assertEqual((first["opened"], first["closed"], first["state"]), (2, 0, "completed"))
            record_member_failure(database, CONCEPT, "885566.TI", DAY_ONE, "Fuyao business error")
            due, _listed = boards_due(database, CONCEPT, DAY_ONE, 10)
            self.assertEqual([row["sector_key"] for row in due], ["885566.TI"])     # failed, still under the cap
            progress = refresh_progress(database, [CONCEPT], DAY_ONE)[CONCEPT]
            self.assertEqual((progress["listed"], progress["completed_or_empty"], progress["failed"]), (2, 1, 1))

            persist_catalog(database, CONCEPT, "同花顺概念（Fuyao）", "cn_concept", [("885431.TI", "新能源汽车")], DAY_TWO)
            second = persist_member_snapshot(database, CONCEPT, "885431.TI", members("999902.SZ", "999903.SZ"),
                                             _at(DAY_TWO, 15, 40))
            self.assertEqual((second["opened"], second["closed"]), (1, 1))
            again = persist_member_snapshot(database, CONCEPT, "885431.TI", members("999902.SZ", "999903.SZ"),
                                            _at(DAY_TWO, 16, 10))
            self.assertEqual((again["opened"], again["closed"]), (0, 0))

            rows = connection.execute(
                """SELECT symbol,effective_from,effective_to,known_at FROM quant.sector_membership_history
                    WHERE taxonomy_key=%s AND sector_key='885431.TI' ORDER BY symbol,effective_from""", (CONCEPT,),
            ).fetchall()
            self.assertEqual([(row["symbol"], row["effective_from"], row["effective_to"]) for row in rows], [
                ("999901.SZ", DAY_ONE, DAY_ONE), ("999902.SZ", DAY_ONE, None), ("999903.SZ", DAY_TWO, None),
            ])
            # The unchanged member keeps the moment it was first known.
            self.assertEqual(rows[1]["known_at"], _at(DAY_ONE, 15, 40))
            predicate = point_in_time_membership_predicate("member")
            for day, expected in ((DAY_ONE, ["999901.SZ", "999902.SZ"]), (DAY_TWO, ["999902.SZ", "999903.SZ"])):
                visible = connection.execute(
                    f"""SELECT symbol FROM quant.sector_membership_history member
                         WHERE taxonomy_key=%s AND sector_key='885431.TI' AND {predicate} ORDER BY symbol""",
                    (CONCEPT, day, day, day),
                ).fetchall()
                self.assertEqual([row["symbol"] for row in visible], expected, day)
            self.assertEqual(boards_due(database, CONCEPT, DAY_TWO, 10)[0], [])

        self.run_rolled_back(body)

    def test_concept_strength_candidates_and_flows_read_back_under_their_own_taxonomies(self):
        from app.close_board_flow_repository import concept_close_snapshot, longhu_close_boards, persist_board_observations
        from app.concept_limit_candidate_repository import concept_memberships, latest_limit_up_pool, persist_candidates
        from app.concept_limit_strength import concept_strength
        from app.fuyao_ths_membership_repository import persist_catalog, persist_member_snapshot
        from app.instrument_registry import InstrumentRecord, ensure_instruments
        from app.board_research_service import run as board_research
        from app.request_models import BoardResearchRunRequest
        from app.sector_read_model import concept_limit_candidates, concept_sector_signals, sector_flows

        pool_symbols = ("999911.SZ", "999912.SZ")

        async def run_database(action, *args, **_kwargs):
            return action(*args)

        async def completed(result=None):
            return result or {"status": "completed"}

        async def study(symbol, request):
            return {"symbol": symbol, "as_of_date": str(request.as_of_date), "technical": {}, "analyst": {"summary": {}},
                    "combined": {}, "sources": [], "events": {}}

        def body(database, connection):
            persist_catalog(database, CONCEPT, "同花顺概念（Fuyao）", "cn_concept",
                            [("885431.TI", "新能源汽车"), ("885338.TI", "融资融券")], DAY_ONE)
            for sector_key in ("885431.TI", "885338.TI"):
                persist_member_snapshot(database, CONCEPT, sector_key,
                                        {symbol: {"name": "测"} for symbol in (*pool_symbols, "999913.SZ")},
                                        _at(DAY_ONE, 15, 40))
            ensure_instruments(connection, [InstrumentRecord(symbol=symbol, exchange="SZ", source="test")
                                            for symbol in pool_symbols], source="test")
            for minute, symbols in ((58, pool_symbols), (59, pool_symbols[:1])):
                for symbol in symbols:
                    connection.execute(
                        """INSERT INTO quant.market_events(event_id,symbol,event_type,occurred_at,available_at,source,title,body,
                                                           event_identity_key)
                           VALUES(gen_random_uuid(),%s,'limit_up_pool',%s,%s,'fuyao_ths',%s,%s,%s)""",
                        (symbol, _at(DAY_TWO, 14, minute), _at(DAY_TWO, 14, minute), f"涨停池：{symbol}",
                         json.dumps({"capability": "a_share_limit_up_pool", "thscode": symbol, "name": "测",
                                     "continue_day_cnt": 2, "max_seal_money": 1.5e8, "limit_up_reason": "测试题材"}),
                         f"test:{symbol}:{minute}"),
                    )
            day, snapshot_at, pool = latest_limit_up_pool(database, DAY_TWO)
            self.assertEqual((day, snapshot_at, sorted(pool)), (DAY_TWO, _at(DAY_TWO, 14, 59), ["999911.SZ"]))
            memberships, counts, labels = concept_memberships(database, DAY_TWO, sorted(pool))
            # 融资融券 is a qualification list, never a concept's strength.
            self.assertEqual([row["sector_key"] for row in memberships], ["885431.TI"])
            self.assertEqual((counts, labels), ({"885431.TI": 3}, {"885431.TI": "新能源汽车"}))
            concepts = concept_strength(pool, memberships, counts, labels)
            stored, _per_concept = persist_candidates(database, DAY_TWO, snapshot_at, concepts, pool, 3, _at(DAY_TWO, 18, 35))
            self.assertEqual(stored, 1)
            candidates = concept_limit_candidates(database, DAY_TWO, 10)
            self.assertEqual((candidates["taxonomy_key"], len(candidates["items"])), (CONCEPT, 1))
            item = dict(candidates["items"][0])
            self.assertEqual((item["board_limit_up_count"], item["board_strength_rank"], item["limit_tag"]), (1, 1, "2天2板"))
            self.assertIsNone(item["board_net_amount"])
            research = asyncio.run(board_research(
                BoardResearchRunRequest(trade_date=DAY_TWO, max_stock_studies=2, sync_announcements=False),
                database=database, run_database=run_database, sync_concept_signals=completed,
                sync_concept_limit_candidates=lambda _request: completed({
                    "status": "completed", "trade_date": str(DAY_TWO), "concepts": [{"sector_key": "885431.TI"}]}),
                sync_announcements=completed, build_stock_study=study, date_for=date.fromisoformat,
            ))
            self.assertEqual([entry["candidate"]["board_limit_up_count"] for entry in research["studies"]], [1])

            connection.execute(
                """INSERT INTO quant.intraday_board_reports(observed_at,status,source_status,summary,payload)
                   VALUES(%s,'completed',%s,'{}'::jsonb,%s)""",
                (_at(DAY_TWO, 16, 2), Json({"provider": "longhuvip_composite"}), Json({"items": [
                    {"sector_key": "999121", "label": "测试行业", "change_pct": 3.45, "net_inflow": 4.5e8, "mapped_members": 12},
                ]})),
            )
            connection.execute(
                """INSERT INTO quant.intraday_board_flow_snapshots(snapshot_minute,observed_at,status,coverage,source_status,payload)
                   VALUES(%s,%s,'completed',%s,'{}'::jsonb,%s)""",
                (_at(DAY_TWO, 14, 59), _at(DAY_TWO, 14, 59, 20), Json({"concept": {"flow_boards": 1}}), Json({
                    "items": [{"taxonomy_key": "eastmoney_concept", "sector_key": "测试概念板", "label": "测试概念板",
                               "net_inflow": 12.35, "change_pct": 3.21}],
                    "unit": "100m_cny", "providers": {"concept": "eastmoney_free"}})),
            )
            report_at, boards = longhu_close_boards(database, DAY_TWO)
            self.assertEqual((report_at, boards[0]["sector_key"]), (_at(DAY_TWO, 16, 2), "999121"))
            persist_board_observations(
                database, taxonomy_key="longhu_ths_industry", taxonomy_label="开盘啦行业板块",
                provider_key="longhuvip_composite", trade_date=DAY_TWO, available_at=report_at,
                rows=[{"sector_key": "999121", "label": "测试行业", "change_pct": 3.45, "net_amount": 4.5e8,
                       "constituent_count": 12, "raw": {"net_amount_unit": "yuan"}}],
                owns_taxonomy=False, taxonomy_metadata={"semantic": "industry_membership"},
            )
            snapshot_at, items, context = concept_close_snapshot(database, DAY_TWO)
            self.assertEqual((snapshot_at, len(items), context["provider"]), (_at(DAY_TWO, 14, 59, 20), 1, "eastmoney_free"))
            persist_board_observations(
                database, taxonomy_key="eastmoney_concept", taxonomy_label="东方财富概念板块", provider_key="eastmoney_free",
                trade_date=DAY_TWO, available_at=snapshot_at,
                rows=[{"sector_key": "测试概念板", "label": "测试概念板", "change_pct": 3.21, "net_amount": 12.35, "raw": {}}],
                owns_taxonomy=False, taxonomy_metadata={"source": "eastmoney", "kind": "concept"},
            )
            persist_board_observations(
                database, taxonomy_key="fuyao_ths_concept_limit_strength", taxonomy_label="同花顺概念涨停强度",
                provider_key="fuyao_ths", trade_date=DAY_TWO, available_at=_at(DAY_TWO, 18, 25),
                rows=[{"sector_key": "885431.TI", "label": "新能源汽车", "constituent_count": 3,
                       "leading_symbol": "999911.SZ", "leading_label": "测", "raw": concepts[0]}],
                owns_taxonomy=True, taxonomy_metadata={"derivation": "fuyao_limit_up_pool_x_fuyao_ths_concept"},
            )
            industry = sector_flows(database, "longhu_ths_industry", DAY_TWO, 10)
            self.assertEqual([(row["sector_key"], float(row["net_amount"])) for row in industry["items"]], [("999121", 4.5e8)])
            retired = sector_flows(database, "ths_industry", DAY_TWO, 10)
            self.assertEqual(retired["superseded_by"], "longhu_ths_industry")
            signals = concept_sector_signals(database, DAY_TWO, 10)
            self.assertEqual((signals["taxonomy_key"], signals["strength"]["status"]), ("eastmoney_concept", "not_joined"))
            self.assertEqual([row["sector_key"] for row in signals["items"]], ["测试概念板"])
            strength = sector_flows(database, "fuyao_ths_concept_limit_strength", DAY_TWO, 10)
            self.assertEqual([(row["sector_key"], row["leading_symbol"]) for row in strength["items"]],
                             [("885431.TI", "999911.SZ")])

        self.run_rolled_back(body)

    def test_membership_refresh_status_reports_the_fuyao_taxonomies(self):
        from app.fuyao_ths_membership_repository import persist_catalog, persist_member_snapshot
        from app.sector_read_model import concept_member_backfill_status

        def body(database, _connection):
            persist_catalog(database, CONCEPT, "同花顺概念（Fuyao）", "cn_concept",
                            [("885431.TI", "新能源汽车"), ("885566.TI", "大飞机")], DAY_ONE)
            persist_member_snapshot(database, CONCEPT, "885431.TI", {"999921.SZ": {"name": "测"}}, _at(DAY_ONE, 15, 40))
            status = concept_member_backfill_status(database, DAY_ONE, automatic_enabled=True, batch_size=25)
            self.assertEqual((status["taxonomy_key"], status["source"]), (CONCEPT, "fuyao_ths"))
            self.assertEqual((status["total_concepts"], status["mapped_concepts"], status["receipt_mapped_concepts"]), (2, 1, 1))
            self.assertFalse(status["complete"])
            self.assertEqual(status["taxonomies"][CONCEPT]["listed"], 2)
            self.assertEqual(status["automatic"], {"enabled": True, "batch_size": 25})

        self.run_rolled_back(body)


if __name__ == "__main__":
    unittest.main()
