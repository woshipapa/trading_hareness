"""Board flows come from captured evidence under each vendor's own taxonomy,
and one source's gap or slow write never silences another."""

from __future__ import annotations

import asyncio
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace

from app.ths_sector_flows import CONCEPT_PERSIST_TIMEOUT_SECONDS, sync_concept_signals, sync_industry

DAY = date(2026, 10, 9)
AT = datetime(2026, 10, 9, 10, 30, tzinfo=timezone.utc)
SNAPSHOT_AT = datetime(2026, 10, 9, 6, 59, 30, tzinfo=timezone.utc)   # 14:59:30 Shanghai
POOL_AT = datetime(2026, 10, 9, 7, 0, tzinfo=timezone.utc)
CONCEPT_ITEMS = [
    {"taxonomy_key": "eastmoney_concept", "sector_key": "人形机器人", "label": "人形机器人", "net_inflow": 12.35, "change_pct": 3.21},
    {"taxonomy_key": "eastmoney_concept", "sector_key": "固态电池", "label": "固态电池", "net_inflow": -4.1, "change_pct": -0.8},
]
POOL = {
    "600127.SH": {"capability": "a_share_limit_up_pool", "thscode": "600127.SH", "name": "金健米业",
                  "continue_day_cnt": 2, "max_seal_money": 215363530.68, "limit_up_reason": "粮油食品+粮食储备"},
    "000981.SZ": {"capability": "a_share_limit_up_pool", "thscode": "000981.SZ", "name": "山子高科",
                  "continue_day_cnt": 1, "max_seal_money": 80000000, "limit_up_reason": "汽车拆解"},
}
MEMBERSHIPS = [{"sector_key": "885431.TI", "symbol": "000981.SZ"}, {"sector_key": "885566.TI", "symbol": "600127.SH"}]
LONGHU_BOARDS = [
    {"sector_key": "881121", "label": "半导体", "strength": 1234.0, "change_pct": 3.45, "amount": 1.2e10,
     "net_inflow": 4.5e8, "volume_ratio": 1.1, "taxonomy_key": "longhu_ths_industry", "mapped_members": 160,
     "top_stocks": [{"symbol": "688981.SH", "name": "中芯国际", "pct_change": 6.1, "net_inflow": 9.1e8}]},
    {"sector_key": "881139", "label": "家居用品", "change_pct": -0.5, "net_inflow": None, "mapped_members": 40},
]


def _name(action):
    return getattr(action, "__name__", None) or action.func.__name__


class Database:
    """Answers the repository reads the flow sync makes and records its writes."""

    def __init__(self, *, snapshot=True, pool=True, concept_write_fails=False, report=True):
        self.snapshot, self.pool, self.concept_write_fails, self.report = snapshot, pool, concept_write_fails, report
        self.writes: list[tuple[str, float, dict]] = []

    async def run(self, action, *args, timeout_seconds=10):
        name = _name(action)
        if name == "concept_close_snapshot":
            if not self.snapshot:
                return None, [], {"latest_concept_snapshot_at": "2026-10-09T05:30:00+00:00"}
            return SNAPSHOT_AT, [dict(item) for item in CONCEPT_ITEMS], {"unit": "100m_cny", "provider": "eastmoney_free"}
        if name == "latest_limit_up_pool":
            return (DAY, POOL_AT, dict(POOL)) if self.pool else (DAY, None, {})
        if name == "concept_memberships":
            return list(MEMBERSHIPS), {"885431.TI": 120, "885566.TI": 40}, {"885431.TI": "新能源汽车", "885566.TI": "大飞机"}
        if name == "longhu_close_boards":
            return (POOL_AT, [dict(item) for item in LONGHU_BOARDS]) if self.report else (None, [])
        if name == "persist_board_observations":
            keywords = action.keywords
            if keywords["taxonomy_key"] == "eastmoney_concept" and self.concept_write_fails:
                raise TimeoutError("database executor timed out")
            self.writes.append((keywords["taxonomy_key"], timeout_seconds, keywords))
            return len(keywords["rows"])
        raise AssertionError(f"unexpected database action {name}")


def run_concepts(database):
    return asyncio.run(sync_concept_signals(
        SimpleNamespace(trade_date=DAY, provider="auto"), trade_date=lambda: DAY,
        run_database_blocking=database.run, db=None, now_utc=lambda: AT,
    ))


class ConceptSignalTests(unittest.TestCase):
    def test_a_failed_concept_write_still_leaves_limit_strength_written(self):
        database = Database(concept_write_fails=True)
        result = run_concepts(database)
        self.assertEqual(result["sources"]["concept_flow"]["status"], "failed")
        self.assertEqual(result["sources"]["limit_strength"]["status"], "completed")
        self.assertEqual([(taxonomy, timeout) for taxonomy, timeout, _ in database.writes],
                         [("fuyao_ths_concept_limit_strength", CONCEPT_PERSIST_TIMEOUT_SECONDS)])
        self.assertEqual(result["status"], "partial")

    def test_both_writes_get_more_than_the_ten_second_default(self):
        database = Database()
        result = run_concepts(database)
        self.assertEqual(result["status"], "completed")
        self.assertEqual([(taxonomy, timeout) for taxonomy, timeout, _ in database.writes],
                         [("eastmoney_concept", CONCEPT_PERSIST_TIMEOUT_SECONDS),
                          ("fuyao_ths_concept_limit_strength", CONCEPT_PERSIST_TIMEOUT_SECONDS)])
        self.assertGreater(CONCEPT_PERSIST_TIMEOUT_SECONDS, 10)

    def test_each_source_names_its_own_taxonomy_and_the_one_it_replaces(self):
        sources = run_concepts(Database())["sources"]
        self.assertEqual((sources["concept_flow"]["taxonomy_key"], sources["concept_flow"]["requested_taxonomy_key"]),
                         ("eastmoney_concept", "ths_concept_flow"))
        self.assertEqual((sources["limit_strength"]["taxonomy_key"], sources["limit_strength"]["requested_taxonomy_key"]),
                         ("fuyao_ths_concept_limit_strength", "ths_limit_strength"))
        self.assertEqual(sources["concept_flow"]["units"], {"net_amount": "100m_cny"})

    def test_concept_rows_keep_the_capture_keys_and_flow(self):
        database = Database()
        run_concepts(database)
        concept = next(keywords for taxonomy, _, keywords in database.writes if taxonomy == "eastmoney_concept")
        self.assertFalse(concept["owns_taxonomy"])            # the membership writer owns the board rows
        self.assertEqual(concept["provider_key"], "eastmoney_free")
        self.assertEqual(concept["available_at"], SNAPSHOT_AT)
        self.assertEqual([(row["sector_key"], row["net_amount"]) for row in concept["rows"]],
                         [("人形机器人", 12.35), ("固态电池", -4.1)])

    def test_strength_counts_sealed_point_in_time_members(self):
        database = Database()
        run_concepts(database)
        strength = next(keywords for taxonomy, _, keywords in database.writes
                        if taxonomy == "fuyao_ths_concept_limit_strength")
        self.assertTrue(strength["owns_taxonomy"])
        first = strength["rows"][0]
        self.assertEqual(first["leading_symbol"], first["raw"]["limit_up_symbols"][0])
        self.assertEqual({row["sector_key"]: row["constituent_count"] for row in strength["rows"]},
                         {"885431.TI": 120, "885566.TI": 40})

    def test_no_closing_snapshot_is_reported_not_written(self):
        result = run_concepts(Database(snapshot=False))
        flow = result["sources"]["concept_flow"]
        self.assertEqual(flow["status"], "unavailable")
        self.assertEqual(flow["latest_concept_snapshot_at"], "2026-10-09T05:30:00+00:00")
        self.assertEqual(result["status"], "partial")

    def test_nothing_available_blocks_rather_than_completing_empty(self):
        database = Database(snapshot=False, pool=False)
        result = run_concepts(database)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(database.writes, [])


class IndustryFlowTests(unittest.TestCase):
    def run_industry(self, database):
        return asyncio.run(sync_industry(
            SimpleNamespace(trade_date=None, provider="super"), trade_date=lambda: DAY,
            run_database_blocking=database.run, db=None,
        ))

    def test_longhu_close_boards_become_longhu_industry_observations(self):
        database = Database()
        result = self.run_industry(database)
        self.assertEqual(result["status"], "completed")
        self.assertEqual((result["taxonomy_key"], result["requested_taxonomy_key"]), ("longhu_ths_industry", "ths_industry"))
        self.assertEqual(result["units"], {"net_amount": "yuan"})
        (taxonomy, _timeout, keywords), = database.writes
        self.assertEqual((taxonomy, keywords["provider_key"]), ("longhu_ths_industry", "longhuvip_composite"))
        self.assertFalse(keywords["owns_taxonomy"])
        # A board without a net inflow is dropped, never stored as zero flow.
        self.assertEqual([(row["sector_key"], row["net_amount"], row["constituent_count"]) for row in keywords["rows"]],
                         [("881121", 4.5e8, 160)])
        self.assertEqual(keywords["available_at"], POOL_AT)

    def test_no_close_report_blocks_and_writes_nothing(self):
        database = Database(report=False)
        result = self.run_industry(database)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["taxonomy_key"], "longhu_ths_industry")
        self.assertEqual(database.writes, [])


if __name__ == "__main__":
    unittest.main()
