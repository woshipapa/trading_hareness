import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
import unittest

from app.concept_limit_candidate_service import ConceptLimitCandidateDependencies, run

DAY = date(2026, 10, 9)
CLOSE = datetime(2026, 10, 9, 7, 0, tzinfo=timezone.utc)          # 15:00 Shanghai
MORNING = datetime(2026, 10, 9, 2, 30, tzinfo=timezone.utc)       # 10:30 Shanghai
POOL = {
    "600127.SH": {"thscode": "600127.SH", "name": "金健米业", "continue_day_cnt": 2, "max_seal_money": 2.1e8,
                  "limit_up_reason": "粮油食品+粮食储备"},
    "000981.SZ": {"thscode": "000981.SZ", "name": "山子高科", "continue_day_cnt": 1, "max_seal_money": 8.0e7},
    "002528.SZ": {"thscode": "002528.SZ", "name": "英飞拓", "continue_day_cnt": 1, "max_seal_money": 3.0e7},
}
MEMBERSHIPS = [
    {"sector_key": "885431.TI", "symbol": "000981.SZ"}, {"sector_key": "885431.TI", "symbol": "002528.SZ"},
    {"sector_key": "885566.TI", "symbol": "600127.SH"},
]


class Database:
    def __init__(self, *, snapshot_at=CLOSE, pool=None, memberships=None):
        self.snapshot_at = snapshot_at
        self.pool = POOL if pool is None else pool
        self.memberships = MEMBERSHIPS if memberships is None else memberships
        self.calls: list[str] = []
        self.persisted: list[tuple] = []

    async def run(self, action, *args, timeout_seconds=10):
        self.calls.append(action.__name__)
        if action.__name__ == "latest_limit_up_pool":
            return (DAY, self.snapshot_at, dict(self.pool)) if self.snapshot_at else (DAY, None, {})
        if action.__name__ == "concept_memberships":
            return list(self.memberships), {"885431.TI": 120, "885566.TI": 40}, {"885431.TI": "新能源汽车", "885566.TI": "大飞机"}
        if action.__name__ == "persist_candidates":
            self.persisted.append(args)
            concepts, leaders = args[3], args[5]
            return sum(min(leaders, len(item["limit_up_symbols"])) for item in concepts), [
                {"sector_key": item["sector_key"], "stored": min(leaders, len(item["limit_up_symbols"]))} for item in concepts]
        raise AssertionError(action.__name__)


def candidates(database, **fields):
    request = SimpleNamespace(**{"trade_date": DAY, "provider": "super", "top_concepts": 8, "leaders_per_concept": 3, **fields})
    return asyncio.run(run(request, ConceptLimitCandidateDependencies(
        run_database=database.run, database=None, now_utc=lambda: datetime(2026, 10, 9, 10, 35, tzinfo=timezone.utc),
    )))


class ConceptLimitCandidateServiceTests(unittest.TestCase):
    def test_concepts_are_ranked_by_sealed_members_from_stored_evidence_only(self):
        database = Database()
        result = candidates(database)
        self.assertEqual(database.calls, ["latest_limit_up_pool", "concept_memberships", "persist_candidates"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual((result["taxonomy_key"], result["requested_taxonomy_key"]), ("fuyao_ths_concept", "ths_concept_flow"))
        self.assertEqual(result["limit_provider"], "fuyao_ths")
        concepts = database.persisted[0][3]
        self.assertEqual([item["sector_key"] for item in concepts], ["885431.TI", "885566.TI"])
        self.assertEqual(concepts[0]["limit_up_symbols"], ["000981.SZ", "002528.SZ"])   # larger seal leads
        self.assertTrue(result["limit_snapshot_is_close"])
        self.assertEqual(result["candidates"], 3)
        self.assertFalse(result["decision_eligible"])

    def test_top_concepts_bounds_the_concepts_written(self):
        database = Database()
        result = candidates(database, top_concepts=1, leaders_per_concept=1)
        self.assertEqual([item["sector_key"] for item in database.persisted[0][3]], ["885431.TI"])
        self.assertEqual(result["concepts_with_limit_ups"], 2)

    def test_an_intraday_snapshot_is_labelled_as_not_the_close(self):
        self.assertFalse(candidates(Database(snapshot_at=MORNING))["limit_snapshot_is_close"])

    def test_no_captured_pool_blocks_before_any_membership_read(self):
        database = Database(snapshot_at=None)
        result = candidates(database)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(database.calls, ["latest_limit_up_pool"])

    def test_no_membership_blocks_without_writing_candidates(self):
        database = Database(memberships=[])
        result = candidates(database)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("fuyao_ths_concept", result["reason"])
        self.assertEqual(database.persisted, [])


if __name__ == "__main__":
    unittest.main()
