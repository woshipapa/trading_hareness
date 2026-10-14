"""One eligibility rule for the all-A snapshot, and the leader-flow strategy on a filled scan.

From the review of the TDX fallback (docs/work/probes/2026-10-14-tdx-fallback-review.md, findings
1 and 5).  The scan tests drive a minute the chain filled through the scan's own capture and
``run_watchlist_scan`` to the leader-flow runtime.
"""

from __future__ import annotations

import asyncio
import dataclasses
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app import all_a_snapshot_sources as sources
from app.all_a_snapshot_sources import scan_snapshot_decision_eligible, snapshot_decision_eligible
from app.datasources.contracts import CapabilityEvidence
from app.fuyao_provider import FuyaoProviderError
from app.intraday_features import annotate_flow_snapshot_provenance
from app.intraday_quote_normalization import quote_from_fuyao
from app.intraday_watch_quote_capture import capture_watch_quotes
from app.intraday_watchlist_scan_service import (
    LEADER_FLOW_SNAPSHOT_NOT_ELIGIBLE_REASON, run_watchlist_scan,
)
from app.platform.evidence_contracts import materialize_evidence_status

import test_all_a_snapshot_sources as chain_tests
import test_intraday_watch_quote_capture as capture_tests
import test_intraday_watchlist_scan_service as scan_tests
import test_xiaojie_leader_flow_runtime as leader_tests

OBSERVED = datetime(2026, 10, 13, 2, 0, tzinfo=timezone.utc)
CACHE_STATUS = {"status": "fresh", "age_seconds": 0.0, "ttl_seconds": 30.0}
FUYAO_ROW = {"symbol": "000001.SZ", "price": 10.0, "pct_change": 1.2, "turnover": 5.0, "volume": 100.0,
             "price_source": "fuyao_ths_all_a_snapshot", "price_observed_at": OBSERVED.isoformat(),
             "raw": {"thscode": "000001.SZ"}}
FUYAO_STATUS = {
    "status": "fresh", "age_seconds": 1.0, "max_age_seconds": 45.0, "source": "fuyao_ths_all_a_snapshot",
    "provider_key": "fuyao_ths", "decision_eligible": True, "scope": "all_a_cross_section",
    "cross_sectional": True, "total": 1, "matched_rows": 1,
}


def fuyao_down():
    return FuyaoProviderError("fuyao down", code=5001, request_id="req-1")


class EligibilityRuleTests(unittest.TestCase):
    """One helper: a present flag must be exactly True; a missing one is eligible only for the main source."""

    def test_a_present_flag_must_be_exactly_true(self):
        self.assertTrue(snapshot_decision_eligible({"decision_eligible": True, "provider_key": "tdx_public"}))
        for value in (False, None, 1, "true", "True"):
            with self.subTest(value=value):
                self.assertFalse(snapshot_decision_eligible({"decision_eligible": value, "provider_key": "fuyao_ths"}))

    def test_a_missing_flag_is_eligible_only_when_the_status_names_no_other_source(self):
        for status in (None, {}, {"status": "fresh"}, {"provider_key": "fuyao_ths"},
                       {"provider_key": None, "fallback_for": None},
                       {"status": "cached", "source": "fuyao_ths_all_a_snapshot", "provider_key": "fuyao_ths"}):
            with self.subTest(status=status):
                self.assertTrue(snapshot_decision_eligible(status))
        for status in ({"provider_key": "tdx_public"},
                       {"provider_key": "fuyao_ths", "fallback_for": "fuyao_ths"},
                       {"fallback_for": "fuyao_ths"}, {"fallback_for": ""}, {"provider_key": ""},
                       {"provider_key": "some_other_source"}, "fresh", ["decision_eligible"], 1):
            with self.subTest(status=status):
                self.assertFalse(snapshot_decision_eligible(status))

    def test_a_rebuilt_status_that_kept_its_provider_and_lost_the_flag_is_not_eligible(self):
        _rows, metadata = chain_tests.Calls(fuyao=fuyao_down(), tdx=chain_tests.tdx_evidence()).read()
        with patch.object(sources, "tdx_snapshot_decision_eligible", return_value=True):
            _rows, promoted = chain_tests.Calls(fuyao=fuyao_down(), tdx=chain_tests.tdx_evidence()).read()
        self.assertFalse(snapshot_decision_eligible(metadata))
        self.assertTrue(snapshot_decision_eligible(promoted))
        # A wrapper that projected the status and dropped the flag does not make the minute eligible,
        # whatever the binding says.
        for kept in (("source", "provider_key"), ("source", "fallback_for"), ("provider_key", "fallback_for", "status")):
            rebuilt = {key: promoted[key] for key in kept}
            with self.subTest(kept=kept):
                self.assertFalse(snapshot_decision_eligible(rebuilt))
                self.assertFalse(scan_snapshot_decision_eligible(rebuilt))

    def test_the_flow_annotation_applies_the_same_rule(self):
        def eligible(status):
            quotes = {"000001.SZ": {"main_net_inflow": 100}}
            annotate_flow_snapshot_provenance(quotes, {**CACHE_STATUS, "cross_sectional": True, **status})
            return quotes["000001.SZ"]["flow_snapshot"]["decision_eligible"]

        self.assertTrue(eligible({}))
        self.assertTrue(eligible({"provider_key": "fuyao_ths"}))
        self.assertTrue(eligible({"provider_key": "tdx_public", "fallback_for": "fuyao_ths", "decision_eligible": True}))
        self.assertFalse(eligible({"provider_key": "tdx_public"}))
        self.assertFalse(eligible({"source": "tdx_public_all_a_snapshot", "fallback_for": "fuyao_ths"}))
        self.assertFalse(eligible({"provider_key": "fuyao_ths", "decision_eligible": None}))

    def test_a_scan_status_is_judged_on_what_the_snapshot_said_not_on_its_contract(self):
        # The contract's own flag is False for the main source too: it speaks of the price as a quote.
        main = materialize_evidence_status("fuyao_all_a_snapshot", FUYAO_STATUS)
        self.assertIs(main["decision_eligible"], False)
        self.assertTrue(scan_snapshot_decision_eligible(main))
        filled = materialize_evidence_status("tdx_public_all_a_snapshot", {"fallback_for": "fuyao_ths"})
        self.assertFalse(scan_snapshot_decision_eligible({**filled, "flow_decision_eligible": False}))
        self.assertTrue(scan_snapshot_decision_eligible({**filled, "flow_decision_eligible": True}))
        self.assertFalse(scan_snapshot_decision_eligible(filled))
        for stub in (None, {}, {"status": "fresh", "cross_sectional": True}, {"status": "unavailable"}):
            self.assertTrue(scan_snapshot_decision_eligible(stub))


def filled_snapshot():
    """A minute the chain filled, as ``main.intraday_all_a_snapshot`` hands it on (cache status over it)."""
    evidence = CapabilityEvidence(
        rows=[chain_tests.TDX_ROW] * 5535, available_at_max=OBSERVED, warnings=("no_trade_rows=44",))
    rows, metadata = chain_tests.Calls(fuyao=fuyao_down(), tdx=evidence).read()
    return rows, {**metadata, **CACHE_STATUS}


class LeaderFlowOnAFilledScanTests(unittest.TestCase):
    """A filled scan, from the chain through the scan's real capture to the leader-flow runtime."""

    def scan(self, snapshot, *, watched):
        rows, status = snapshot
        flow, patches, delivered, _marked = leader_tests.build(sent=0)
        terminal, persisted_status, statuses = [], [], []

        async def all_a_snapshot():
            return [dict(row) for row in rows], dict(status)

        async def nothing(*_args, **_kwargs):
            return []

        capture_dependencies = dataclasses.replace(
            capture_tests.WatchQuoteCaptureTests.dependencies(
                all_a_snapshot=all_a_snapshot, tencent_watch_quotes=nothing, sina_quotes=nothing,
                eastmoney_watch_flows=nothing, calls=[]),
            quote_from_all_a=quote_from_fuyao, annotate_flow_provenance=annotate_flow_snapshot_provenance,
        )

        async def capture(symbols, observed_at, slo):
            captured = await capture_watch_quotes(symbols, observed_at, slo, capture_dependencies)
            statuses.append(captured.all_a_snapshot_status)
            return captured

        async def session():
            return True, "continuous auction"

        async def watches(_):
            return [{"symbol": "000001.SZ"}] if watched else []

        async def persist_terminal(*args):
            terminal.append(args)

        async def persist_xiaojie(_scan_id, value):
            persisted_status.append(value)

        async def leader_flow(*, scan_id, observed_at, all_a_rows):
            return await flow.run(scan_id=scan_id, observed_at=observed_at, all_a_rows=all_a_rows)

        async def surge(*_args, **_kwargs):
            return {}, {}

        dependencies = scan_tests.IntradayWatchlistScanServiceTests.dependencies(
            now_utc=lambda: OBSERVED, realtime_session=session, load_watches=watches, capture_quotes=capture,
            persist_terminal=persist_terminal, persist_xiaojie_status=persist_xiaojie,
            xiaojie_leader_flow=leader_flow, surge_context=surge,
            build_source_status=lambda **kwargs: {"all_a_snapshot": kwargs["all_a_snapshot_status"]},
        )
        request = SimpleNamespace(symbols=[], realtime_validation_offset=0, realtime_validation_limit=4)
        with ExitStack() as stack:
            mocks = [stack.enter_context(item) for item in patches]
            result = asyncio.run(run_watchlist_scan(request, dependencies))
        evaluated, recorded, events = mocks[0], mocks[1], mocks[5]
        stored = terminal[0][4]["xiaojie_leader_flow"] if terminal else persisted_status[0]
        return SimpleNamespace(result=result, leader=result["xiaojie_leader_flow"], stored=stored,
                               evaluated=evaluated, recorded=recorded, events=events, delivered=delivered,
                               snapshot_status=statuses[0])

    def test_a_filled_scan_records_no_candidate_and_delivers_no_alert(self):
        for watched in (False, True):
            with self.subTest(watched=watched):
                run = self.scan(filled_snapshot(), watched=watched)
                self.assertEqual(run.result["status"], "completed")
                # The scan did get the fallback's rows, under the fallback's own contract.
                self.assertEqual(run.snapshot_status["provider_key"], "tdx_public")
                self.assertIs(run.snapshot_status["flow_decision_eligible"], False)
                self.assertEqual(run.leader, {
                    "status": "skipped",
                    "reason": f"{LEADER_FLOW_SNAPSHOT_NOT_ELIGIBLE_REASON}: tdx_public",
                    "snapshot_source": "tdx_public_all_a_snapshot", "snapshot_provider": "tdx_public",
                    "fallback_for": "fuyao_ths",
                })
                self.assertEqual(run.stored, run.leader)
                run.evaluated.assert_not_called()
                run.recorded.assert_not_called()
                run.events.assert_not_called()
                self.assertEqual(run.delivered, [])

    def test_a_main_source_scan_runs_the_strategy_as_before(self):
        for watched in (False, True):
            with self.subTest(watched=watched):
                run = self.scan(([FUYAO_ROW], dict(FUYAO_STATUS)), watched=watched)
                self.assertEqual(run.snapshot_status, materialize_evidence_status("fuyao_all_a_snapshot", FUYAO_STATUS))
                self.assertEqual(run.leader["status"], "completed")
                self.assertEqual((run.leader["candidates"], run.leader["new_candidates"], run.leader["alerted"]),
                                 (5, 5, 5))
                self.assertEqual(run.evaluated.call_args.args[0], [FUYAO_ROW])
                run.recorded.assert_called_once()
                self.assertEqual(run.events.call_count, 5)
                self.assertEqual(len(run.delivered), 5)
                self.assertEqual(run.stored, run.leader)

    def test_a_failed_read_is_skipped_for_the_reason_it_always_was(self):
        run = self.scan(([], {"status": "unavailable"}), watched=False)
        self.assertEqual(run.leader, {"status": "skipped", "reason": "no all-A cross-section in this scan"})
        run.recorded.assert_not_called()

    def test_a_filled_scan_runs_the_strategy_once_the_binding_is_eligible(self):
        with patch.object(sources, "tdx_snapshot_decision_eligible", return_value=True):
            snapshot = filled_snapshot()
        run = self.scan(snapshot, watched=False)
        self.assertIs(run.snapshot_status["flow_decision_eligible"], True)
        self.assertEqual(run.leader["status"], "completed")
        run.recorded.assert_called_once()

if __name__ == "__main__":
    unittest.main()
