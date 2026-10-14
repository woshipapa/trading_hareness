import asyncio
import dataclasses
import gc
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import all_a_snapshot_sources as sources
from app.all_a_snapshot_sources import (
    AllASnapshotFallbackError, compare, main_source_reference, project_tdx_snapshot_rows,
    tdx_snapshot_completeness,
)
from app.datasources.contracts import CapabilityEvidence
from app.fuyao_provider import FuyaoProviderError, all_a_snapshot_rows
from app.intraday_cross_section import SharedAsyncSnapshot
from app.public_provider_rate_limits import PublicProviderRateLimited


FIXTURE = Path(__file__).parent / "fixtures" / "all_a_snapshot_fuyao_tdx_20261012.json"
READ_AT = datetime(2026, 10, 12, 7, 31, 49, tzinfo=timezone.utc)
TDX_ROW = {
    "symbol": "000001.SZ", "code": "000001", "price": 11.73, "pre_close": 11.59,
    "open": 11.62, "high": 11.83, "low": 11.62, "pct_change": 1.2079,
    "volume_lots": 100, "amount": 117300.0,
}


class TdxSnapshotCompletenessTests(unittest.TestCase):
    def test_measured_2026_10_13_snapshots_use_the_main_source_roster(self):
        empty = tdx_snapshot_completeness(0, ["no_trade_rows=5579"], 5580)
        pre_open = tdx_snapshot_completeness(5480, ["no_trade_rows=99"], 5580)
        continuous = tdx_snapshot_completeness(5535, ["no_trade_rows=44"], 5580)

        self.assertEqual(empty, {
            "priced_rows": 0, "no_trade_rows": 5579, "roster_total": 5580,
            "roster": 5580, "share": 0.0, "complete": False,
        })
        self.assertAlmostEqual(pre_open["share"], 5480 / 5580)
        self.assertTrue(pre_open["complete"])
        self.assertAlmostEqual(continuous["share"], 5535 / 5580)
        self.assertTrue(continuous["complete"])

    def test_short_reads_are_incomplete(self):
        for priced_rows, no_trade_rows in ((3000, 10), (5000, 0), (5200, 20)):
            result = tdx_snapshot_completeness(
                priced_rows, [f"no_trade_rows={no_trade_rows}"], 5580,
            )
            self.assertFalse(result["complete"])
        self.assertTrue(tdx_snapshot_completeness(5301, [], 5580)["complete"])

    def test_missing_main_source_roster_fails_closed(self):
        result = tdx_snapshot_completeness(5579, [], None)
        self.assertEqual(result["roster"], 5579)
        self.assertEqual(result["share"], 1.0)
        self.assertFalse(result["complete"])


class TdxSnapshotProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE.read_text())
        cls.observed_at = datetime(2026, 10, 12, 7, 31, 46, tzinfo=timezone.utc)
        cls.projected = project_tdx_snapshot_rows(cls.fixture["tdx_rows"], cls.observed_at)
        cls.by_symbol = {row["symbol"]: row for row in cls.projected}
        cls.fuyao_by_symbol = {row["symbol"]: row for row in cls.fixture["fuyao_rows"]}

    def test_projects_common_rows_into_fuyao_shape(self):
        self.assertEqual(len(self.projected), len(self.fixture["tdx_rows"]))
        for symbol, fuyao in self.fuyao_by_symbol.items():
            projected = self.by_symbol[symbol]
            for field in ("symbol", "price", "pct_change", "price_source", "price_observed_at"):
                expected = self.observed_at.isoformat() if field == "price_observed_at" else fuyao[field]
                if field == "price_source":
                    expected = "tdx_public_all_a_snapshot"
                if field == "pct_change":
                    self.assertAlmostEqual(projected[field], expected, places=5, msg=symbol)
                else:
                    self.assertEqual(projected[field], expected, symbol)
            self.assertAlmostEqual(projected["volume"], fuyao["volume"], delta=100, msg=symbol)
            self.assertAlmostEqual(projected["turnover"], fuyao["turnover"], delta=1000, msg=symbol)
            self.assertEqual(projected["raw"]["thscode"], symbol)
            self.assertEqual(projected["raw"]["ticker"], next(row["code"] for row in self.fixture["tdx_rows"] if row["symbol"] == symbol))
            tdx = next(row for row in self.fixture["tdx_rows"] if row["symbol"] == symbol)
            self.assertEqual(projected["raw"]["last_price"], tdx["price"])
            self.assertEqual(projected["raw"]["prev_price"], tdx["pre_close"])
            self.assertEqual(projected["raw"]["open_price"], tdx["open"])
            self.assertEqual(projected["raw"]["high_price"], tdx["high"])
            self.assertEqual(projected["raw"]["low_price"], tdx["low"])
            self.assertEqual(projected["raw"]["price_change"], round(tdx["price"] - tdx["pre_close"], 2))
            self.assertEqual(projected["raw"]["price_change_ratio_pct"], tdx["pct_change"])
            self.assertEqual(projected["raw"]["volume"], projected["volume"])
            self.assertEqual(projected["raw"]["turnover"], projected["turnover"])

    def test_projects_symbol_missing_from_fuyao(self):
        row = self.by_symbol["600293.SH"]
        self.assertEqual(row["symbol"], "600293.SH")
        self.assertEqual(row["volume"], 0)
        self.assertEqual(row["turnover"], 0.0)

    def test_drops_non_positive_prices(self):
        rows = [{"symbol": "000001.SZ", "price": 0, "pre_close": 1, "pct_change": -100,
                 "volume_lots": 1, "amount": 1, "open": 1, "high": 1, "low": 1}]
        self.assertEqual(project_tdx_snapshot_rows(rows, self.observed_at), [])


class TdxSnapshotComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = json.loads(FIXTURE.read_text())
        cls.fuyao_rows = fixture["fuyao_rows"]
        cls.tdx_rows = project_tdx_snapshot_rows(fixture["tdx_rows"], datetime(2026, 10, 12, tzinfo=timezone.utc))

    def test_compares_fixture_without_verdict(self):
        result = compare(self.fuyao_rows, self.tdx_rows, 2.5)

        self.assertEqual(result["symbols_in_both"], 14)
        self.assertEqual(result["only_in_fuyao"], [])
        self.assertEqual(result["only_in_tdx"], ["600293.SH"])
        self.assertEqual(result["price"]["within_one_tick"], 1.0)
        self.assertEqual(result["price"]["within_0_1_pct"], 1.0)
        self.assertEqual(result["price"]["within_0_5_pct"], 1.0)
        self.assertEqual(result["price"]["largest_relative_gap"], 0.0)
        self.assertEqual(result["price"]["largest_relative_gap_symbol"], "000001.SZ")
        self.assertLess(result["pct_change"]["largest_absolute_gap"], 0.000001)
        self.assertAlmostEqual(result["volume"]["median_ratio"], 0.9999990450538223)
        self.assertEqual(result["volume"]["within_1_pct"], 1.0)
        self.assertAlmostEqual(result["turnover"]["median_ratio"], 0.9999999892985673)
        self.assertEqual(result["turnover"]["within_1_pct"], 1.0)
        self.assertEqual(result["seconds_apart"], 2.5)

    def test_moved_price_changes_only_price_measurements(self):
        moved = [dict(row) for row in self.tdx_rows]
        moved[0]["price"] = 11.80

        result = compare(self.fuyao_rows, moved, 1.0)

        self.assertEqual(result["price"]["within_one_tick"], 13 / 14)
        self.assertEqual(result["price"]["within_0_1_pct"], 13 / 14)
        self.assertEqual(result["price"]["within_0_5_pct"], 13 / 14)
        self.assertAlmostEqual(result["price"]["largest_relative_gap"], 0.07 / 11.73)
        self.assertEqual(result["price"]["largest_relative_gap_symbol"], "000001.SZ")
        self.assertLess(result["pct_change"]["largest_absolute_gap"], 0.000001)
        self.assertEqual(result["seconds_apart"], 1.0)


def tdx_evidence(priced_rows=5535, no_trade_rows=44):
    return CapabilityEvidence(
        rows=[TDX_ROW] * priced_rows, available_at_max=READ_AT,
        warnings=("tdx_host=test", *((f"no_trade_rows={no_trade_rows}",) if no_trade_rows else ())),
    )


class Calls:
    """Async stubs for the chain's three reads, counting what was asked."""

    def __init__(self, *, fuyao=None, tdx=None, roster=5580):
        self.fuyao_result, self.tdx_result, self.roster_result = fuyao, tdx, roster
        self.fuyao_calls = self.tdx_calls = self.roster_calls = 0

    @staticmethod
    def _outcome(value):
        if isinstance(value, BaseException):
            raise value
        return value

    async def fuyao(self):
        self.fuyao_calls += 1
        return self._outcome(self.fuyao_result)

    async def tdx(self):
        self.tdx_calls += 1
        return self._outcome(self.tdx_result)

    async def roster(self):
        self.roster_calls += 1
        return self._outcome(self.roster_result)

    def read(self):
        return asyncio.run(sources.read(self.fuyao, self.tdx, self.roster))


class SnapshotMetadataTests(unittest.TestCase):
    def test_the_fuyao_snapshot_names_its_provider_and_keeps_what_it_returned(self):
        page = {"timestamp": 1787625155000, "total": 1,
                "item": [{"thscode": "600519.SH", "last_price": 1305.99, "volume": 1, "turnover": 2}]}

        async def fetch(_capability, _params):
            return page

        with patch("app.fuyao_provider.fetch", fetch):
            rows, metadata = asyncio.run(all_a_snapshot_rows(
                now=datetime.fromtimestamp(1787625156, tz=timezone.utc)))

        self.assertEqual(metadata["provider_key"], "fuyao_ths")
        self.assertIs(metadata["decision_eligible"], True)
        self.assertEqual(
            {key: metadata[key] for key in ("source", "status", "total", "matched_rows", "cross_sectional")},
            {"source": "fuyao_ths_all_a_snapshot", "status": "fresh", "total": 1, "matched_rows": 1,
             "cross_sectional": True},
        )
        self.assertEqual(rows[0]["price_source"], "fuyao_ths_all_a_snapshot")

    def test_the_tdx_snapshot_is_eligible_exactly_when_its_catalog_binding_is(self):
        self.assertIs(sources.tdx_snapshot_decision_eligible(), False)
        promoted = tuple(
            dataclasses.replace(item, decision_eligible=True)
            if (item.source, item.capability) == ("tdx_public", "quote.all_a_snapshot") else item
            for item in sources.BINDINGS
        )
        completeness = tdx_snapshot_completeness(5535, ["no_trade_rows=44"], 5580)
        with patch.object(sources, "BINDINGS", promoted):
            eligible = sources.tdx_snapshot_metadata(
                completeness=completeness, fallback_reason="x", available_at=READ_AT)
        with patch.object(sources, "BINDINGS", ()):
            unbound = sources.tdx_snapshot_metadata(
                completeness=completeness, fallback_reason="x", available_at=READ_AT)
        self.assertIs(eligible["decision_eligible"], True)
        self.assertIs(unbound["decision_eligible"], False)

    def test_the_reference_is_a_main_source_minute_or_nothing(self):
        at = READ_AT - timedelta(seconds=2)
        fuyao = (at, [{"symbol": "000001.SZ", "snapshot_metadata": {"provider_key": "fuyao_ths"}}])
        for other in (
            None, (at, []),
            (at, [{"symbol": "000001.SZ", "snapshot_metadata": {"provider_key": "tdx_public"}}]),
            (at, [{"symbol": "000001.SZ", "snapshot_metadata": {"age_seconds": 3.0}}]),
            (at, [{"symbol": "000001.SZ"}]),
        ):
            self.assertIsNone(main_source_reference(other))
        self.assertIs(main_source_reference(fuyao), fuyao)


class FallbackChainTests(unittest.TestCase):
    def test_a_fuyao_answer_is_returned_and_tdx_is_not_read(self):
        answer = ([{"symbol": "000001.SZ"}], {"provider_key": "fuyao_ths"})
        calls = Calls(fuyao=answer, tdx=tdx_evidence())

        self.assertIs(calls.read(), answer)
        self.assertEqual((calls.tdx_calls, calls.roster_calls), (0, 0))

    def test_a_fuyao_provider_error_is_filled_by_one_labelled_tdx_read(self):
        calls = Calls(fuyao=FuyaoProviderError("rate_limited:fuyao_ths " + "x" * 400), tdx=tdx_evidence())

        rows, metadata = calls.read()

        self.assertEqual((calls.fuyao_calls, calls.tdx_calls, calls.roster_calls), (1, 1, 1))
        self.assertEqual(len(rows), 5535)
        self.assertEqual(rows[0]["price_source"], "tdx_public_all_a_snapshot")
        self.assertEqual(rows[0]["price_observed_at"], READ_AT.isoformat())
        self.assertEqual(rows[0]["volume"], 10000)
        self.assertEqual(metadata["source"], "tdx_public_all_a_snapshot")
        self.assertEqual(metadata["provider_key"], "tdx_public")
        self.assertIs(metadata["decision_eligible"], False)
        self.assertEqual(metadata["fallback_for"], "fuyao_ths")
        self.assertTrue(metadata["fallback_reason"].startswith("rate_limited:fuyao_ths"))
        self.assertLessEqual(len(metadata["fallback_reason"]), sources.FALLBACK_REASON_MAX_CHARS)
        self.assertEqual(metadata["completeness"], tdx_snapshot_completeness(5535, ["no_trade_rows=44"], 5580))
        self.assertEqual((metadata["total"], metadata["matched_rows"]), (5579, 5535))
        self.assertEqual((metadata["status"], metadata["upstream_timestamp_ms"]), ("unknown", None))
        self.assertEqual(metadata["available_at"], READ_AT.isoformat())
        self.assertTrue(metadata["cross_sectional"])
        self.assertEqual(metadata["warnings"], ["tdx_host=test", "no_trade_rows=44"])

    def test_another_fuyao_exception_propagates_and_tdx_is_not_read(self):
        calls = Calls(fuyao=ValueError("bad page"), tdx=tdx_evidence())

        with self.assertRaisesRegex(ValueError, "bad page") as caught:
            calls.read()
        self.assertNotIsInstance(caught.exception, FuyaoProviderError)
        self.assertEqual((calls.tdx_calls, calls.roster_calls), (0, 0))

    def assert_not_filled(self, calls, reason):
        with self.assertRaises(AllASnapshotFallbackError) as caught:
            calls.read()
        error = caught.exception
        self.assertIsInstance(error, FuyaoProviderError)
        self.assertIs(error.__cause__, calls.fuyao_result)
        self.assertIn(reason, error.fallback_reason)
        self.assertIn("fuyao down", str(error))
        self.assertEqual((error.code, error.request_id), (5001, "req-1"))
        self.assertEqual(calls.tdx_calls, 1)
        return error

    def test_both_sources_failing_raise_an_error_chained_from_the_fuyao_one(self):
        fuyao_error = FuyaoProviderError("fuyao down", code=5001, request_id="req-1")
        self.assert_not_filled(Calls(fuyao=fuyao_error, tdx=RuntimeError("no TDX host answered")),
                               "tdx_read_failed:RuntimeError: no TDX host answered")

    def test_a_refused_tdx_slot_does_not_fill(self):
        fuyao_error = FuyaoProviderError("fuyao down", code=5001, request_id="req-1")
        self.assert_not_filled(Calls(fuyao=fuyao_error, tdx=PublicProviderRateLimited("tdx_public")),
                               "rate_limited:tdx_public")

    def test_an_incomplete_snapshot_does_not_fill(self):
        for tdx, roster in (
            (tdx_evidence(0, 5579), 5580),                 # the opening call auction
            (tdx_evidence(5200, 20), 5580),                # a read that stopped at a short page
            (tdx_evidence(), None),                        # no roster to measure against
            (tdx_evidence(), TimeoutError("database")),    # the roster read failed
            ([TDX_ROW] * 3000, 5580),                      # bare rows, as an adapter may return
        ):
            fuyao_error = FuyaoProviderError("fuyao down", code=5001, request_id="req-1")
            with self.subTest(roster=roster, rows=len(getattr(tdx, "rows", tdx))):
                self.assert_not_filled(Calls(fuyao=fuyao_error, tdx=tdx, roster=roster),
                                       "tdx_snapshot_incomplete:")

    def test_a_tdx_read_that_never_answers_is_left_within_the_bound_and_not_started_twice(self):
        release = asyncio.Event()
        reads = []
        loop_errors = []

        async def never_answers():
            reads.append("read")
            await release.wait()
            raise RuntimeError("late tdx failure")

        async def fuyao():
            raise FuyaoProviderError("fuyao down")

        async def roster():
            return 5580

        async def scenario():
            loop = asyncio.get_running_loop()
            loop.set_exception_handler(lambda _loop, context: loop_errors.append(context))
            shared = SharedAsyncSnapshot(never_answers, ttl_seconds=5.0, clock=loop.time)

            async def tdx():
                return (await shared.get())[0]

            started = loop.time()
            reasons = []
            for _ in range(2):
                with self.assertRaises(AllASnapshotFallbackError) as caught:
                    await sources.read(fuyao, tdx, roster)
                reasons.append(caught.exception.fallback_reason)
            waited = loop.time() - started
            release.set()
            for _ in range(5):
                await asyncio.sleep(0)
            gc.collect()
            await asyncio.sleep(0)
            return reasons, waited

        with patch.object(sources, "TDX_READ_TIMEOUT_SECONDS", 0.02):
            reasons, waited = asyncio.run(scenario())
        gc.collect()
        self.assertEqual(reasons, ["tdx_read_timeout:0.02s"] * 2)
        self.assertLess(waited, 1.0)
        self.assertEqual(reads, ["read"])
        self.assertEqual(loop_errors, [])
