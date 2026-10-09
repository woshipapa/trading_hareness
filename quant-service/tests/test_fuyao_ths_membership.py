"""The Fuyao THS membership refresh: listing, pacing, batching and receipts."""

from __future__ import annotations

import asyncio
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace

from app.fuyao_provider import FuyaoProviderError
from app.fuyao_ths_membership import (
    FuyaoThsMembershipDependencies, REQUEST_SPACING_SECONDS, constituent_members, index_catalog_rows, run_batch,
    sync_catalog, sync_concept_members,
)
from app.public_provider_rate_limits import PublicProviderRateLimited

NOW = datetime(2026, 10, 9, 7, 40, tzinfo=timezone.utc)            # 15:40 Shanghai
TODAY = date(2026, 10, 9)
LISTINGS = {
    "cn_concept": {"item": [{"thscode": "885431.TI", "name": "新能源汽车"}, {"thscode": "885566.TI", "name": "大飞机"}],
                   "timestamp": 1789738641661},
    "industry": {"item": [{"thscode": "881121.TI", "name": "半导体"}]},
    "region": {"item": [{"thscode": "882001.TI", "name": "北京"}]},
}
CONSTITUENTS = {"timestamp": 1789738642255, "item": [
    {"thscode": "000009.SZ", "ticker": "000009", "name": "中国宝安"},
    {"thscode": "000021.SZ", "ticker": "000021", "name": "深科技"},
    {"thscode": "885431.TI", "ticker": "885431", "name": "不是个股"},
]}


def _refused() -> FuyaoProviderError:
    try:
        raise PublicProviderRateLimited("fuyao_ths")
    except PublicProviderRateLimited as cause:
        error = FuyaoProviderError(str(cause))
        error.__cause__ = cause
        return error


class State:
    """In-memory stand-in for the catalogue rows and per-board receipts."""

    def __init__(self, *, listed=None, settled=()):
        self.listed: dict[str, list[tuple[str, str]]] = dict(listed or {})
        self.receipts: dict[tuple[str, str], str] = {key: "completed" for key in settled}
        self.calls: list[str] = []
        self.snapshots: list[tuple[str, str, list[str], datetime]] = []

    async def run(self, action, *args, timeout_seconds=10):
        name = action.__name__
        self.calls.append(name)
        if name == "listed_counts":
            _db, keys, _day = args
            return {key: len(self.listed.get(key, [])) for key in keys if self.listed.get(key)}
        if name == "persist_catalog":
            _db, taxonomy_key, _label, _tag, boards, listed_on = args
            assert listed_on == TODAY
            self.listed[taxonomy_key] = list(boards)
            return len(boards)
        if name == "boards_due":
            _db, taxonomy_key, _day, limit = args
            due = [{"sector_key": code, "label": label} for code, label in self.listed.get(taxonomy_key, [])
                   if self.receipts.get((taxonomy_key, code)) not in {"completed", "empty"}]
            return due[:limit], len(self.listed.get(taxonomy_key, []))
        if name == "boards_page":
            _db, taxonomy_key, _day, offset, limit = args
            rows = [{"sector_key": code, "label": label} for code, label in self.listed.get(taxonomy_key, [])]
            return rows[offset:offset + limit], len(rows)
        if name == "persist_member_snapshot":
            _db, taxonomy_key, sector_key, members, observed_at = args
            self.snapshots.append((taxonomy_key, sector_key, sorted(members), observed_at))
            state = "completed" if members else "empty"
            self.receipts[(taxonomy_key, sector_key)] = state
            return {"members": len(members), "opened": len(members), "closed": 0, "state": state}
        if name == "record_member_failure":
            _db, taxonomy_key, sector_key, _day, _detail = args
            self.receipts[(taxonomy_key, sector_key)] = "failed"
            return None
        if name == "refresh_progress":
            _db, keys, _day = args
            return {key: {"listed": len(self.listed.get(key, [])),
                          "completed_or_empty": sum(1 for (taxonomy, _), state in self.receipts.items()
                                                    if taxonomy == key and state in {"completed", "empty"}),
                          "failed": sum(1 for (taxonomy, _), state in self.receipts.items() if taxonomy == key and state == "failed"),
                          "remaining": 0} for key in keys}
        raise AssertionError(name)


class Provider:
    def __init__(self, *, refuse_after=None, fail_codes=()):
        self.requests: list[tuple[str, dict]] = []
        self.refuse_after, self.fail_codes = refuse_after, set(fail_codes)

    async def fetch(self, capability, params):
        self.requests.append((capability, dict(params)))
        if self.refuse_after is not None and len(self.requests) > self.refuse_after:
            raise _refused()
        if capability == "ths_index_list":
            return LISTINGS[params["tag"]]
        if params["thscode"] in self.fail_codes:
            raise FuyaoProviderError("Unknown thscode: " + params["thscode"], code=4004)
        return CONSTITUENTS


def deps(state, provider, *, configured=True, sleeps=None):
    async def sleep(seconds):
        if sleeps is not None:
            sleeps.append(seconds)

    return FuyaoThsMembershipDependencies(
        run_database=state.run, database=None, fetch=provider.fetch, configured=lambda: configured,
        now_utc=lambda: NOW, sleep=sleep,
    )


def batch(size=25, **fields):
    return SimpleNamespace(**{"batch_size": size, "refresh_flow_catalog": False, "trade_date": None, "provider": "auto", **fields})


class ParsingTests(unittest.TestCase):
    def test_constituents_keep_equities_only_with_their_raw_row(self):
        members = constituent_members(CONSTITUENTS, "885431.TI")
        self.assertEqual(sorted(members), ["000009.SZ", "000021.SZ"])
        self.assertEqual(members["000009.SZ"], {"name": "中国宝安", "thscode": "000009.SZ", "index_code": "885431.TI"})

    def test_catalog_rows_are_sorted_and_deduplicated(self):
        boards, skipped = index_catalog_rows({"item": [{"thscode": "885566.TI", "name": "大飞机"},
                                                       {"thscode": "885431.ti", "name": "新能源汽车"},
                                                       {"thscode": "885566.TI", "name": "大飞机"}]})
        self.assertEqual(boards, [("885431.TI", "新能源汽车"), ("885566.TI", "大飞机")])
        self.assertEqual(skipped, 1)


class BatchTests(unittest.TestCase):
    def test_a_first_batch_lists_each_tag_then_refreshes_concepts_first(self):
        state, provider, sleeps = State(), Provider(), []
        result = asyncio.run(run_batch(batch(3), deps(state, provider, sleeps=sleeps)))
        self.assertEqual(result["status"], "completed")
        self.assertEqual([params.get("tag") or params.get("thscode") for _capability, params in provider.requests],
                         ["cn_concept", "industry", "region", "885431.TI", "885566.TI", "881121.TI"])
        # Every request after the first waits its turn on the shared Fuyao budget.
        self.assertEqual(sleeps, [REQUEST_SPACING_SECONDS] * 5)
        self.assertEqual([item["taxonomy_key"] for item in result["member_results"]],
                         ["fuyao_ths_concept", "fuyao_ths_concept", "fuyao_ths_industry"])
        self.assertEqual(result["provider_requests"], 6)
        self.assertEqual(result["refresh_date"], "2026-10-09")

    def test_a_listed_day_is_not_listed_again_and_settled_boards_are_skipped(self):
        state = State(listed={"fuyao_ths_concept": [("885431.TI", "新能源汽车"), ("885566.TI", "大飞机")],
                              "fuyao_ths_industry": [("881121.TI", "半导体")], "fuyao_ths_region": [("882001.TI", "北京")]},
                      settled=[("fuyao_ths_concept", "885431.TI")])
        provider = Provider()
        result = asyncio.run(run_batch(batch(2), deps(state, provider)))
        self.assertEqual([params for _capability, params in provider.requests],
                         [{"thscode": "885566.TI"}, {"thscode": "881121.TI"}])
        self.assertEqual(result["progress"]["completed_or_empty"], 3)

    def test_refresh_flow_catalog_forces_a_new_listing(self):
        state = State(listed={"fuyao_ths_concept": [("885431.TI", "新能源汽车")]})
        provider = Provider()
        asyncio.run(run_batch(batch(0, refresh_flow_catalog=True), deps(state, provider)))
        self.assertEqual([params["tag"] for _capability, params in provider.requests], ["cn_concept", "industry", "region"])

    def test_a_refused_start_defers_the_rest_without_failing_a_board(self):
        state = State(listed={"fuyao_ths_concept": [("885431.TI", "新能源汽车"), ("885566.TI", "大飞机")],
                              "fuyao_ths_industry": [("881121.TI", "半导体")], "fuyao_ths_region": [("882001.TI", "北京")]})
        provider = Provider(refuse_after=1)
        result = asyncio.run(run_batch(batch(25), deps(state, provider)))
        self.assertEqual([item["status"] for item in result["member_results"]], ["completed", "deferred"])
        self.assertEqual(result["status"], "partial")
        self.assertNotIn("record_member_failure", state.calls)
        self.assertNotIn(("fuyao_ths_concept", "885566.TI"), state.receipts)
        # One start plus the bounded retries of the next, then the batch stops.
        self.assertEqual(len(provider.requests), 1 + 4)

    def test_a_provider_error_fails_one_board_and_the_batch_continues(self):
        state = State(listed={"fuyao_ths_concept": [("885431.TI", "新能源汽车"), ("885566.TI", "大飞机")],
                              "fuyao_ths_industry": [("881121.TI", "半导体")], "fuyao_ths_region": [("882001.TI", "北京")]})
        result = asyncio.run(run_batch(batch(25), deps(state, Provider(fail_codes={"885431.TI"}))))
        self.assertEqual([item["status"] for item in result["member_results"]],
                         ["failed", "completed", "completed", "completed"])
        self.assertEqual(state.receipts[("fuyao_ths_concept", "885431.TI")], "failed")
        self.assertEqual(result["status"], "partial")

    def test_each_snapshot_is_known_from_its_own_response(self):
        state = State(listed={"fuyao_ths_concept": [("885431.TI", "新能源汽车")],
                              "fuyao_ths_industry": [("881121.TI", "半导体")], "fuyao_ths_region": [("882001.TI", "北京")]})
        moments = iter([NOW, datetime(2026, 10, 9, 7, 40, 2, tzinfo=timezone.utc),
                        datetime(2026, 10, 9, 7, 40, 4, tzinfo=timezone.utc), datetime(2026, 10, 9, 7, 40, 6, tzinfo=timezone.utc)])
        dependencies = FuyaoThsMembershipDependencies(
            run_database=state.run, database=None, fetch=Provider().fetch, configured=lambda: True,
            now_utc=lambda: next(moments), sleep=lambda _seconds: asyncio.sleep(0),
        )
        asyncio.run(run_batch(batch(3), dependencies))
        self.assertEqual([snapshot[3].second for snapshot in state.snapshots], [2, 4, 6])

    def test_without_a_key_nothing_is_requested_or_written(self):
        state, provider = State(), Provider()
        result = asyncio.run(run_batch(batch(25), deps(state, provider, configured=False)))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual((provider.requests, state.calls), ([], []))
        self.assertIsNone(result["progress"]["remaining"])

    def test_a_past_trade_date_is_answered_with_what_was_refreshed(self):
        state = State(listed={"fuyao_ths_concept": [("885431.TI", "新能源汽车")]})
        result = asyncio.run(run_batch(batch(0, trade_date=date(2026, 9, 30)), deps(state, Provider())))
        self.assertIn("2026-09-30", result["notice"])
        self.assertEqual(result["trade_date"], "2026-10-09")


class RouteTests(unittest.TestCase):
    def test_catalog_route_maps_the_tushare_type_to_its_fuyao_tag(self):
        state, provider = State(), Provider()
        result = asyncio.run(sync_catalog(SimpleNamespace(
            index_type="I", sync_members=True, member_offset=0, member_limit=5, resume=False,
        ), deps(state, provider)))
        self.assertEqual((result["taxonomy_key"], result["fuyao_tag"], result["sectors"]),
                         ("fuyao_ths_industry", "industry", 1))
        self.assertEqual([item["sector_key"] for item in result["member_results"]], ["881121.TI"])
        self.assertIsNone(result["next_member_offset"])

    def test_a_resume_batch_lists_the_tag_only_once_a_day(self):
        state, provider = State(listed={"fuyao_ths_concept": [("885431.TI", "新能源汽车")]}), Provider()
        result = asyncio.run(sync_catalog(SimpleNamespace(
            index_type="N", sync_members=True, member_offset=0, member_limit=5, resume=True,
        ), deps(state, provider)))
        self.assertEqual([capability for capability, _params in provider.requests], ["ths_index_constituents"])
        self.assertEqual((result["listed_now"], result["sectors"], result["status"]), (False, 1, "completed"))

    def test_catalog_route_reports_types_fuyao_does_not_list(self):
        result = asyncio.run(sync_catalog(SimpleNamespace(index_type="S"), deps(State(), Provider())))
        self.assertEqual(result["status"], "unavailable")

    def test_a_failed_listing_writes_nothing(self):
        class Broken(Provider):
            async def fetch(self, capability, params):
                raise FuyaoProviderError("Fuyao business error", code=5000)

        state = State()
        result = asyncio.run(sync_catalog(SimpleNamespace(index_type="N", sync_members=False, member_offset=0,
                                                          member_limit=0, resume=False), deps(state, Broken())))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(state.calls, [])

    def test_concept_member_route_resumes_from_receipts(self):
        state = State(listed={"fuyao_ths_concept": [("885431.TI", "新能源汽车"), ("885566.TI", "大飞机")]},
                      settled=[("fuyao_ths_concept", "885431.TI")])
        result = asyncio.run(sync_concept_members(SimpleNamespace(
            trade_date=None, refresh_flow_catalog=False, member_offset=0, member_limit=5, resume=True,
        ), deps(state, Provider())))
        self.assertEqual([item["sector_key"] for item in result["member_results"]], ["885566.TI"])
        self.assertEqual((result["taxonomy_key"], result["total_concepts"]), ("fuyao_ths_concept", 2))
        self.assertIsNone(result["next_member_offset"])

    def test_concept_member_route_blocks_when_the_listing_is_unavailable(self):
        class Empty(Provider):
            async def fetch(self, capability, params):
                return {"item": []}

        state = State()
        result = asyncio.run(sync_concept_members(SimpleNamespace(
            trade_date=None, refresh_flow_catalog=False, member_offset=0, member_limit=5, resume=False,
        ), deps(state, Empty())))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(state.calls, ["listed_counts"])


if __name__ == "__main__":
    unittest.main()
