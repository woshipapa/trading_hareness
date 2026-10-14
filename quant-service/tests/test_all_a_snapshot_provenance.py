"""Consumers use what the all-A snapshot says about its source (W008 B2).

Each site is pinned twice: with a main-source (Fuyao) snapshot, whose output is what it was before
snapshots named their source, and with a minute the TDX fallback filled. Where eligibility is
involved the fallback is pinned on both sides of its catalog binding's ``decision_eligible``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import unittest
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from app import all_a_snapshot_sources as snapshot_sources
from app import board_flow_drill_runtime as drill_runtime
from app import intraday_signal_rules as rules_module
from app import level1_snapshot_runtime as level1
from app import minute_cross_section
from app.all_a_snapshot_sources import (
    fallback_provenance, snapshot_decision_eligible, tdx_snapshot_completeness, tdx_snapshot_metadata,
)
from app.board_flow_drill_runtime import BoardDrillDependencies, drill_board_stock_candidates
from app.datasources import catalog as catalog_module
from app.datasources.catalog import (
    BINDINGS, EXCHANGE_TIMESTAMPED_QUOTE_LABELS, RULE_USABLE_FLOW_LABELS, SOURCE_LABELS, binding_decision_eligible,
    health_capability,
)
from app.datasources.contracts import LIVE_VERIFIED
from app.intraday_derived_flow_metrics import (
    DERIVED_FLOW_LABELS, UNKNOWN_VOLUME_DERIVED_LABEL, apply_derived_watch_flow_metrics, derived_flow_label,
)
from app.indicator_health import minute_document_checks
from app.intraday_features import annotate_flow_snapshot_provenance
from app.intraday_quote_normalization import observation_source, quote_from_fuyao
from app.intraday_scan_preparation import prepare_intraday_scan_inputs
from app.intraday_scan_source_status import build_scan_source_status
from app.intraday_watch_quote_capture import capture_watch_quotes
from app.level1_snapshot_runtime import Level1CaptureDependencies, capture_level1_snapshot, level1_capture
from app.limit_up_anchor import anchor_source, live_limit_up_pool_rows, snapshot_anchor_source
from app.live_policy import live_policy_gate
from app.main import intraday_signal_rules
from app.paper_auto_execution_runtime import (
    SNAPSHOT_NOT_ELIGIBLE_REASON, PaperAutoExecutionDependencies, run_paper_auto_execution,
)
from app.platform.evidence_contracts import (
    EVIDENCE_CONTRACTS, all_a_snapshot_contract_keys, materialize_evidence_status,
)
from app.realtime_provider_health import SOURCES as HEALTH_SOURCES, project_realtime_source_health

import test_intraday_watch_quote_capture as capture_tests

FUYAO_ROW = {"symbol": "000001.SZ", "price": 10.0, "pct_change": 1.2, "turnover": 5.0, "volume": 100.0,
             "price_source": "fuyao_ths_all_a_snapshot", "price_observed_at": "2026-10-13T02:00:00+00:00",
             "raw": {"thscode": "000001.SZ"}}
TDX_ROW = {**FUYAO_ROW, "price_source": "tdx_public_all_a_snapshot"}
SNAPSHOT_KEY = ("tdx_public", "quote.all_a_snapshot")


def bindings_with_tdx_snapshot_eligible() -> tuple:
    """The catalog as it is after the operator act: the binding live verified and decision eligible."""
    return tuple(
        dataclasses.replace(item, status=LIVE_VERIFIED, decision_eligible=True)
        if (item.source, item.capability) == SNAPSHOT_KEY else item
        for item in BINDINGS
    )


class QuoteNormalizationTests(unittest.TestCase):
    def test_a_main_source_row_maps_exactly_as_before(self):
        expected = {
            "symbol": "000001.SZ", "name": None, "price": 10.0, "pct_change": 1.2, "turnover": 5.0, "volume": 100.0,
            "raw": {"thscode": "000001.SZ"}, "price_source": "fuyao_ths_all_a_snapshot",
            "price_observed_from_depth": False, "price_observed_at": "2026-10-13T02:00:00+00:00",
        }
        self.assertEqual(quote_from_fuyao(FUYAO_ROW), expected)
        unlabelled = {key: value for key, value in FUYAO_ROW.items() if key != "price_source"}
        self.assertEqual(quote_from_fuyao(unlabelled), expected)
        self.assertEqual(observation_source(expected), "fuyao_ths")

    def test_a_fallback_row_keeps_its_own_source_for_price_and_volume(self):
        quote = quote_from_fuyao(TDX_ROW)
        self.assertEqual(quote["price_source"], "tdx_public_all_a_snapshot")
        self.assertEqual(quote["volume_source"], "tdx_public_all_a_snapshot")
        self.assertEqual(observation_source(quote), "tdx_public")

    def test_neither_snapshot_label_is_a_decision_quote_label(self):
        self.assertEqual(EXCHANGE_TIMESTAMPED_QUOTE_LABELS, {"tencent_batched_watch_quote", "longhuvip_watch_quote"})
        for label in ("fuyao_ths_all_a_snapshot", "tdx_public_all_a_snapshot"):
            self.assertFalse(SOURCE_LABELS[label].exchange_timestamped, label)


class SourceLabelTests(unittest.TestCase):
    def test_the_fallback_labels_belong_to_the_tdx_snapshot_binding(self):
        for label in ("tdx_public_all_a_snapshot", "tdx_public_derived"):
            self.assertEqual((SOURCE_LABELS[label].source, SOURCE_LABELS[label].capability), SNAPSHOT_KEY)
        self.assertFalse(SOURCE_LABELS["tdx_public_all_a_snapshot"].rule_usable_flow)
        self.assertEqual(catalog_module.validate_catalog(), [])

    def test_the_derived_label_is_rule_usable_exactly_when_the_binding_is_eligible(self):
        binding = next(item for item in BINDINGS if (item.source, item.capability) == SNAPSHOT_KEY)
        self.assertEqual(SOURCE_LABELS["tdx_public_derived"].rule_usable_flow, binding.decision_eligible)
        self.assertEqual("tdx_public_derived" in RULE_USABLE_FLOW_LABELS, binding.decision_eligible)
        for bindings in (BINDINGS, bindings_with_tdx_snapshot_eligible(),
                         tuple(item for item in BINDINGS if (item.source, item.capability) != SNAPSHOT_KEY)):
            eligible = binding_decision_eligible(*SNAPSHOT_KEY, bindings)
            labels = {label.label: label for label in catalog_module._labels(bindings)}
            self.assertEqual(labels["tdx_public_derived"].rule_usable_flow, eligible)
            # Nothing else moves with the binding: the main source's label and the quote labels stay.
            self.assertTrue(labels["fuyao_ths_derived"].rule_usable_flow)
            self.assertFalse(labels["tdx_public_all_a_snapshot"].rule_usable_flow)
            self.assertEqual({label for label, item in labels.items() if item.exchange_timestamped},
                             {"tencent_batched_watch_quote", "longhuvip_watch_quote"})
        self.assertTrue(binding_decision_eligible(*SNAPSHOT_KEY, bindings_with_tdx_snapshot_eligible()))
        self.assertFalse(binding_decision_eligible(*SNAPSHOT_KEY, ()))


class DerivedFlowLabelTests(unittest.TestCase):
    derived = {"000001.SZ": {"volume_ratio": 1.92, "turnover_rate": 2.0}}

    def test_a_main_source_quote_is_labelled_as_before(self):
        for quote in ({}, quote_from_fuyao(FUYAO_ROW)):
            sources = apply_derived_watch_flow_metrics({"000001.SZ": quote}, self.derived)
            self.assertEqual(sources["000001.SZ"], {
                "volume_ratio": "fuyao_ths_derived", "turnover_rate": "fuyao_ths_derived",
                "main_net_inflow": "unavailable",
            })

    def test_a_fallback_quote_is_labelled_with_its_own_provider(self):
        quote = quote_from_fuyao(TDX_ROW)
        # A direct watch quote replaces the price label; the volume is still the snapshot's.
        quote["price_source"] = "tencent_batched_watch_quote"
        sources = apply_derived_watch_flow_metrics({"000001.SZ": quote}, self.derived)
        self.assertEqual(sources["000001.SZ"], {
            "volume_ratio": "tdx_public_derived", "turnover_rate": "tdx_public_derived",
            "main_net_inflow": "unavailable",
        })
        self.assertEqual(quote["flow_metric_sources"], sources["000001.SZ"])

    def test_a_named_source_this_module_does_not_know_is_not_passed_off_as_the_main_one(self):
        self.assertEqual(derived_flow_label(None), "fuyao_ths_derived")
        self.assertEqual(derived_flow_label("fuyao_ths_all_a_snapshot"), "fuyao_ths_derived")
        self.assertEqual(derived_flow_label("longhuvip_watch_quote"), "longhuvip_volume_derived")
        self.assertEqual(derived_flow_label("some_other_snapshot"), UNKNOWN_VOLUME_DERIVED_LABEL)
        self.assertNotIn(UNKNOWN_VOLUME_DERIVED_LABEL, SOURCE_LABELS)
        self.assertLessEqual(DERIVED_FLOW_LABELS - {UNKNOWN_VOLUME_DERIVED_LABEL}, set(SOURCE_LABELS))


class SignalRuleFlowLabelTests(unittest.TestCase):
    watch = {"symbol": "000001.SZ", "available_quantity": 0, "entry_price": None,
             "alert_on_entry": True, "alert_on_exit": True}

    def anomaly_fires(self, label):
        quote = {"price": 10.4, "pct_change": 2.4, "volume_ratio": 3.5, "turnover_rate": 6.0, "main_net_inflow": None,
                 "price_source": "tencent_batched_watch_quote", "price_freshness": {"status": "fresh"},
                 "flow_metric_sources": {"volume_ratio": label, "turnover_rate": label,
                                         "main_net_inflow": "unavailable"}}
        signals = intraday_signal_rules(self.watch, quote, {"price": 10.3}, None, None, None)
        return any(item["signal_key"] == "000001.SZ:watch:volume_anomaly" for item in signals)

    def test_main_source_derived_flow_feeds_the_rule_as_before(self):
        self.assertTrue(self.anomaly_fires("fuyao_ths_derived"))

    def test_fallback_derived_flow_feeds_no_rule_while_the_binding_is_not_eligible(self):
        self.assertNotIn("tdx_public_derived", RULE_USABLE_FLOW_LABELS)
        self.assertFalse(self.anomaly_fires("tdx_public_derived"))

    def test_fallback_derived_flow_feeds_the_rule_once_the_binding_is_eligible(self):
        labels = {label.label: label for label in catalog_module._labels(bindings_with_tdx_snapshot_eligible())}
        usable = frozenset(label for label, item in labels.items() if item.rule_usable_flow)
        self.assertEqual(usable, {"fuyao_ths_derived", "tdx_public_derived"})
        with mock.patch.object(rules_module, "RULE_USABLE_FLOW_LABELS", usable):
            self.assertTrue(self.anomaly_fires("tdx_public_derived"))


def cached(**snapshot):
    """A snapshot status as the shared cache hands it out: its own ``status`` and age on top."""
    return {**snapshot, "status": "cached", "age_seconds": 12.0, "ttl_seconds": 30.0}


FUYAO_STATUS = cached(source="fuyao_ths_all_a_snapshot", provider_key="fuyao_ths", decision_eligible=True,
                      scope="all_a_cross_section", cross_sectional=True)


def tdx_status(eligible):
    return cached(source="tdx_public_all_a_snapshot", provider_key="tdx_public", decision_eligible=eligible,
                  fallback_for="fuyao_ths", scope="all_a_cross_section", cross_sectional=True)


class FlowProvenanceTests(unittest.TestCase):
    def flow_snapshot(self, status):
        quotes = {"000001.SZ": {"main_net_inflow": 100}}
        annotate_flow_snapshot_provenance(quotes, status)
        return quotes["000001.SZ"]["flow_snapshot"]

    def test_a_main_source_snapshot_is_judged_as_before(self):
        without_the_flag = {key: value for key, value in FUYAO_STATUS.items() if key != "decision_eligible"}
        self.assertEqual(self.flow_snapshot(FUYAO_STATUS), self.flow_snapshot(without_the_flag))
        self.assertEqual(self.flow_snapshot(FUYAO_STATUS), {
            "status": "cached", "age_seconds": 12.0, "max_age_seconds": 45.0, "decision_eligible": True,
            "source": "fuyao_ths_all_a_snapshot", "scope": "all_a_cross_section", "cross_sectional": True,
            "semantics": "all_a_public_flow_proxy_not_exchange_order_flow",
        })
        self.assertFalse(self.flow_snapshot({**FUYAO_STATUS, "age_seconds": 46.0})["decision_eligible"])

    def test_a_fallback_snapshot_is_eligible_only_when_it_says_so(self):
        # The cache says "cached" and 12 s for both: only the flag tells them apart.
        self.assertFalse(self.flow_snapshot(tdx_status(False))["decision_eligible"])
        self.assertTrue(self.flow_snapshot(tdx_status(True))["decision_eligible"])
        self.assertEqual(self.flow_snapshot(tdx_status(False))["source"], "tdx_public_all_a_snapshot")
        for not_true in (None, "true", 1, 0):
            self.assertFalse(self.flow_snapshot(tdx_status(not_true))["decision_eligible"], not_true)
        self.assertFalse(self.flow_snapshot({**tdx_status(True), "age_seconds": 46.0})["decision_eligible"])


class LivePolicyTests(unittest.TestCase):
    """``live_policy`` has no branch for a source: it reads the flow snapshot's own verdict."""

    def gate(self, status):
        quote = {"price": 10.0, "main_net_inflow": 100, "price_source": "tencent_batched_watch_quote",
                 "price_freshness": {"status": "fresh"}}
        annotate_flow_snapshot_provenance({"000001.SZ": quote}, status)
        return live_policy_gate(
            {"signal_type": "entry"}, {"symbol": "000001.SZ", "available_quantity": 0}, quote,
            {"status": "completed", "trade_constraints": {}},
            {"status": "available", "market_state": "mixed_or_neutral", "board_snapshot_age_seconds": 30},
            {"status": "confirmed"},
        )

    def test_an_entry_on_a_main_source_flow_passes_as_before(self):
        result = self.gate(FUYAO_STATUS)
        self.assertNotIn("public_flow_snapshot_not_fresh", result["reason_codes"])
        self.assertTrue(result["allow_confirmation"])

    def test_an_entry_on_a_fallback_flow_is_rejected_while_the_binding_is_not_eligible(self):
        result = self.gate(tdx_status(False))
        self.assertIn("public_flow_snapshot_not_fresh", result["reason_codes"])
        self.assertFalse(result["allow_confirmation"])
        # Exactly how a stale main-source flow is rejected.
        stale = self.gate({**FUYAO_STATUS, "age_seconds": 46.0})
        self.assertEqual(result["reason_codes"], stale["reason_codes"])
        self.assertEqual(result["risk_flags"], stale["risk_flags"])

    def test_an_entry_on_a_fallback_flow_passes_once_the_binding_is_eligible(self):
        result = self.gate(tdx_status(True))
        self.assertNotIn("public_flow_snapshot_not_fresh", result["reason_codes"])
        self.assertTrue(result["allow_confirmation"])

    def test_a_snapshot_price_is_never_a_decision_quote_whatever_the_binding_says(self):
        for label, status in (("fuyao_ths_all_a_snapshot", FUYAO_STATUS), ("tdx_public_all_a_snapshot", tdx_status(True))):
            quote = {"price": 10.0, "price_source": label, "price_freshness": {"status": "fresh"}}
            result = live_policy_gate(
                {"signal_type": "entry"}, {"symbol": "000001.SZ", "available_quantity": 0}, quote,
                {"status": "completed", "trade_constraints": {}},
                {"status": "available", "market_state": "mixed_or_neutral", "board_snapshot_age_seconds": 30},
                {"status": "confirmed"},
            )
            self.assertIn("quote_source_not_decision_eligible", result["reason_codes"], status["source"])
            self.assertFalse(result["allow_confirmation"])


class WatchQuoteCaptureProvenanceTests(unittest.TestCase):
    """The watch scan's capture hands the snapshot's own eligibility to the flow annotation."""

    reference = {"000001.SZ": {"float_shares": 1000.0, "mean_daily_volume_shares": 100.0}}

    def capture(self, rows, status):
        async def all_a_snapshot():
            if status is None:
                raise ValueError("snapshot unavailable")
            return [dict(row) for row in rows], dict(status)

        async def nothing(*_args, **_kwargs):
            return []

        async def reference(_symbols, _observed_at):
            return self.reference

        annotated = []
        dependencies = dataclasses.replace(
            capture_tests.WatchQuoteCaptureTests.dependencies(
                all_a_snapshot=all_a_snapshot, tencent_watch_quotes=nothing, sina_quotes=nothing,
                eastmoney_watch_flows=nothing, calls=[], watch_flow_reference=reference,
                derive_flow_metrics=lambda quotes, _reference, *, observed_at: {
                    symbol: {"volume_ratio": 1.5, "turnover_rate": 2.0} for symbol in quotes},
            ),
            quote_from_all_a=quote_from_fuyao,
            annotate_flow_provenance=lambda quotes, given: annotated.append(dict(given)),
        )
        capture = asyncio.run(capture_watch_quotes(
            ["000001.SZ"], datetime(2026, 10, 13, 2, tzinfo=timezone.utc), 20.0, dependencies))
        return capture, annotated[0]

    def test_a_main_source_snapshot_is_captured_as_before(self):
        capture, given = self.capture([FUYAO_ROW], FUYAO_STATUS)
        self.assertEqual(capture.all_a_snapshot_status, materialize_evidence_status("fuyao_all_a_snapshot", FUYAO_STATUS))
        self.assertEqual(given, {**capture.all_a_snapshot_status, "decision_eligible": True})
        quote = capture.quotes["000001.SZ"]
        self.assertEqual(quote["price_source"], "fuyao_ths_all_a_snapshot")
        self.assertNotIn("volume_source", quote)
        self.assertEqual(quote["flow_metric_sources"]["volume_ratio"], "fuyao_ths_derived")
        self.assertEqual(capture.derived_flow_status["derived_field_symbols"], {"volume_ratio": 1, "turnover_rate": 1})
        self.assertEqual(capture.derived_flow_status["volume_sources"], ["fuyao_ths_all_a_snapshot"])

    def test_a_fallback_snapshot_reaches_the_flow_annotation_with_what_its_binding_says(self):
        for eligible in (False, True):
            capture, given = self.capture([TDX_ROW], tdx_status(eligible))
            self.assertIs(given["decision_eligible"], eligible)
            self.assertEqual(given["source"], "tdx_public_all_a_snapshot")
            # The stored status keeps the contract's verdict on the price: no decision quote.
            self.assertFalse(capture.all_a_snapshot_status["decision_eligible"])
            quote = capture.quotes["000001.SZ"]
            self.assertEqual(quote["price_source"], "tdx_public_all_a_snapshot")
            self.assertEqual(quote["flow_metric_sources"]["volume_ratio"], "tdx_public_derived")
            self.assertEqual(quote["flow_metric_sources"]["turnover_rate"], "tdx_public_derived")
            self.assertEqual(capture.derived_flow_status["derived_field_symbols"],
                             {"volume_ratio": 1, "turnover_rate": 1})
            self.assertEqual(capture.derived_flow_status["volume_sources"], ["tdx_public_all_a_snapshot"])

    def test_no_snapshot_gives_the_flow_annotation_no_eligibility_to_read(self):
        capture, given = self.capture([], None)
        self.assertEqual(capture.all_a_snapshot_status["status"], "unavailable")
        self.assertNotIn("decision_eligible", given)
        quotes = {"000001.SZ": {"main_net_inflow": 100}}
        annotate_flow_snapshot_provenance(quotes, given)
        self.assertFalse(quotes["000001.SZ"]["flow_snapshot"]["decision_eligible"])

    def test_a_fallback_snapshot_is_materialized_under_its_own_contracts(self):
        capture, _given = self.capture([TDX_ROW], tdx_status(False))
        status = capture.all_a_snapshot_status
        self.assertEqual((status["evidence_contract"], status["provider_key"], status["capability"]),
                         ("tdx_public_all_a_snapshot", "tdx_public", "all_a_snapshot"))
        self.assertEqual(status["fallback_for"], "fuyao_ths")
        self.assertIs(status["flow_decision_eligible"], False)
        self.assertEqual((capture.derived_flow_status["evidence_contract"], capture.derived_flow_status["provider_key"]),
                         ("tdx_public_derived_watch_flow", "tdx_public"))
        eligible, _given = self.capture([TDX_ROW], tdx_status(True))
        self.assertIs(eligible.all_a_snapshot_status["flow_decision_eligible"], True)
        main, _given = self.capture([FUYAO_ROW], FUYAO_STATUS)
        self.assertNotIn("flow_decision_eligible", main.all_a_snapshot_status)
        self.assertEqual((main.derived_flow_status["evidence_contract"], main.derived_flow_status["provider_key"]),
                         ("fuyao_ths_derived_watch_flow", "fuyao_ths"))

    def test_a_provider_without_a_contract_is_not_materialized_as_the_main_source(self):
        with self.assertRaises(ValueError):
            all_a_snapshot_contract_keys({"provider_key": "some_other_source"})
        self.assertEqual(all_a_snapshot_contract_keys({"status": "unavailable"}),
                         ("fuyao_all_a_snapshot", "fuyao_ths_derived_watch_flow"))
        self.assertEqual(all_a_snapshot_contract_keys(None), all_a_snapshot_contract_keys(FUYAO_STATUS))


def scan_status(quotes, rows, snapshot_status):
    return build_scan_source_status(
        selected_symbols=["000001.SZ", "000002.SZ"], quotes=quotes, all_a_rows=rows, fresh_watch_rows=[],
        sina_watch_rows=[], licensed_watch_rows=[], licensed_watch_status={"status": "disabled"},
        eastmoney_watch_flow_rows=[], eastmoney_watch_flow_status={"status": "unavailable"},
        derived_flow_status={"status": "fresh"}, all_a_snapshot_status=snapshot_status, surge_source={},
        priority_symbols=[], rotation_pool_size=0, rotation_start_offset=0, next_rotation_offset=0,
        realtime_minutes={}, fast_confirmations={}, board_cache_evidence={}, quote_timestamp_slo_seconds=20.0,
    )


class ScanSourceStatusTests(unittest.TestCase):
    def test_a_main_source_scan_is_reported_as_before(self):
        snapshot = materialize_evidence_status("fuyao_all_a_snapshot", FUYAO_STATUS)
        status = scan_status({"000001.SZ": {"price_source": "fuyao_ths_all_a_snapshot"},
                              "000002.SZ": {"price_source": "tencent_batched_watch_quote"}}, [FUYAO_ROW], snapshot)
        self.assertEqual(status["fuyao"], {"status": "completed", "rows": 1, "matched": 2, "all_a_snapshot": snapshot,
                                           "all_a_only_watch_quote_symbols": 1})
        self.assertNotIn("tdx_public", status)
        self.assertEqual(list(status)[:2], ["fuyao", "tencent_watch"])

    def test_a_fallback_scan_names_the_provider_that_served_it(self):
        for eligible in (False, True):
            snapshot = {**materialize_evidence_status("tdx_public_all_a_snapshot", tdx_status(eligible)),
                        "fallback_reason": "Fuyao request failed", "flow_decision_eligible": eligible}
            status = scan_status({"000001.SZ": {"price_source": "tdx_public_all_a_snapshot"},
                                  "000002.SZ": {"price_source": "fuyao_ths_all_a_snapshot"}}, [TDX_ROW], snapshot)
            # The main source's block is the block of a scan it did not answer.
            self.assertEqual(status["fuyao"]["status"], "unavailable")
            self.assertEqual((status["fuyao"]["rows"], status["fuyao"]["matched"],
                              status["fuyao"]["all_a_only_watch_quote_symbols"]), (0, 0, 0))
            self.assertEqual(status["fuyao"]["all_a_snapshot"]["status"], "unavailable")
            self.assertEqual(status["fuyao"]["all_a_snapshot"]["error"], "Fuyao request failed")
            self.assertEqual(status["fuyao"]["all_a_snapshot"]["served_by"], "tdx_public")
            self.assertEqual(status["fuyao"]["all_a_snapshot"]["provider_key"], "fuyao_ths")
            served = status["tdx_public"]
            self.assertEqual((served["status"], served["rows"], served["matched"]), ("completed", 1, 2))
            self.assertEqual(served["all_a_only_watch_quote_symbols"], 1)
            self.assertEqual(served["all_a_snapshot"], snapshot)
            self.assertEqual(served["fallback_for"], "fuyao_ths")
            self.assertIs(served["flow_decision_eligible"], eligible)


class ScanPreparationHealthTests(unittest.TestCase):
    def recorded(self, rows, source_status):
        calls = []
        dependencies = mock.MagicMock()
        dependencies.record_provider_success = lambda _connection, *args: calls.append(("success", *args))
        dependencies.record_provider_failure = lambda _connection, *args: calls.append(("failure", *args))
        prepare_intraday_scan_inputs(
            mock.MagicMock(), scan_id=uuid.uuid4(), observed_at=datetime(2026, 10, 13, 2, tzinfo=timezone.utc),
            selected_symbols=[], source_status=source_status, watches=[], quotes={}, all_a_rows=rows,
            quote_latency_ms=7, realtime_minutes={}, surge_features={}, confirmation_window=timedelta(minutes=5),
            dependencies=dependencies,
        )
        return calls

    def test_a_main_source_scan_records_its_success_as_before(self):
        for row in (FUYAO_ROW, {"symbol": "000001.SZ"}):
            self.assertEqual(self.recorded([row], {}), [("success", "fuyao_ths", "realtime_quote", 1, 7)])
        self.assertEqual(self.recorded([], {}), [
            ("failure", "fuyao_ths", "realtime_quote", "all-A snapshot unavailable during watch scan", 7)])

    def test_a_fallback_scan_records_the_main_failure_and_the_serving_provider_success(self):
        source_status = {"tdx_public": {"all_a_snapshot": {"fallback_reason": "Fuyao request failed"}}}
        self.assertEqual(self.recorded([TDX_ROW, TDX_ROW], source_status), [
            ("failure", "fuyao_ths", "realtime_quote", "Fuyao request failed", 7),
            ("success", "tdx_public", "realtime_quote", 2, 7),
        ])
        self.assertEqual(self.recorded([TDX_ROW], {})[0][3], "all-A snapshot unavailable during watch scan")


class SnapshotEligibilityTests(unittest.TestCase):
    def test_eligibility_is_the_flag_and_never_the_status(self):
        self.assertTrue(snapshot_decision_eligible(FUYAO_STATUS))
        self.assertFalse(snapshot_decision_eligible(tdx_status(False)))
        self.assertTrue(snapshot_decision_eligible(tdx_status(True)))
        self.assertEqual(tdx_status(False)["status"], "cached")
        for not_true in (None, "true", 1, 0):
            self.assertFalse(snapshot_decision_eligible(tdx_status(not_true)), not_true)
        # A caller from before the flag hands no key at all.
        for without_the_flag in ({}, None, {"status": "stale"}):
            self.assertTrue(snapshot_decision_eligible(without_the_flag))

    def test_only_a_filled_minute_reports_a_fallback(self):
        self.assertEqual(fallback_provenance(FUYAO_STATUS), {})
        self.assertEqual(fallback_provenance({}), {})
        self.assertEqual(fallback_provenance(tdx_status(False)), {
            "snapshot_source": "tdx_public_all_a_snapshot", "snapshot_provider": "tdx_public", "fallback_for": "fuyao_ths"})

    def test_the_fallback_metadata_carries_the_flag_under_the_cache_status(self):
        completeness = tdx_snapshot_completeness(5500, (), 5580)
        metadata = tdx_snapshot_metadata(completeness=completeness, fallback_reason="x",
                                         available_at=datetime(2026, 10, 13, 2, tzinfo=timezone.utc))
        binding = next(item for item in BINDINGS if (item.source, item.capability) == SNAPSHOT_KEY)
        self.assertIs(metadata["decision_eligible"], binding.decision_eligible)
        # The service hands the metadata out under the cache's status: "unknown" is gone, the flag is not.
        merged = {**metadata, "status": "fresh", "age_seconds": 0.0, "ttl_seconds": 30.0}
        self.assertEqual(snapshot_decision_eligible(merged), binding.decision_eligible)
        with mock.patch.object(snapshot_sources, "BINDINGS", bindings_with_tdx_snapshot_eligible()):
            eligible = tdx_snapshot_metadata(completeness=completeness, fallback_reason="x",
                                             available_at=datetime(2026, 10, 13, 2, tzinfo=timezone.utc))
        self.assertTrue(snapshot_decision_eligible({**eligible, "status": "fresh"}))


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _Database:
    """A paper account with one held position, so a quiet minute still wants quotes."""

    @contextmanager
    def transaction(self):
        connection = mock.MagicMock()
        connection.execute = lambda sql, _params=None: _Result(
            [{"cash": 100000}] if "paper_accounts" in sql
            else [{"symbol": "000001.SZ", "quantity": 100}] if "paper_positions" in sql else [])
        yield connection


async def _run(action, *args, **_kwargs):
    return action(*args)


class PaperExecutionTests(unittest.TestCase):
    def run_paper(self, status, quotes=None):
        reads = []

        async def snapshot():
            reads.append(status)
            return [dict(FUYAO_ROW)], status

        deps = PaperAutoExecutionDependencies(
            enabled=lambda: True, database=_Database(), run_database=_run, run_vendor_blocking=_run,
            vendor_source=lambda: None, all_a_snapshot=snapshot, json_safe=lambda value: value,
            account_equity=1_000_000.0)
        with mock.patch("app.paper_auto_execution.plan_paper_orders",
                        return_value={"buys": [], "sells": [{"symbol": "000001.SZ"}], "skipped": []}) as plan, \
                mock.patch("app.paper_order_bridge.execute_plan", return_value={"orders": 1}) as place:
            result = asyncio.run(run_paper_auto_execution([], quotes, deps))
        return result, plan.call_count, place.call_count, len(reads)

    def test_a_main_source_snapshot_is_acted_on_as_before(self):
        for status in (FUYAO_STATUS, {}):
            result, planned, placed, _reads = self.run_paper(status)
            self.assertEqual((result["status"], planned, placed), ("executed", 1, 1))
            self.assertEqual(result["placed"], {"orders": 1})

    def test_no_order_is_planned_on_a_fallback_snapshot_while_its_binding_is_not_eligible(self):
        result, planned, placed, reads = self.run_paper(tdx_status(False))
        self.assertEqual((planned, placed, reads), (0, 0, 1))
        self.assertEqual(result, {
            "status": "blocked", "reason": SNAPSHOT_NOT_ELIGIBLE_REASON, "suppressed_repeat": 0,
            "snapshot_source": "tdx_public_all_a_snapshot", "snapshot_provider": "tdx_public",
            "fallback_for": "fuyao_ths"})

    def test_a_fallback_snapshot_is_acted_on_once_its_binding_is_eligible(self):
        result, planned, placed, _reads = self.run_paper(tdx_status(True))
        self.assertEqual((result["status"], planned, placed), ("executed", 1, 1))


class BoardDrillTests(unittest.TestCase):
    def drill(self, status):
        handed = []

        async def snapshot():
            return [dict(TDX_ROW if (status or {}).get("fallback_for") else FUYAO_ROW)], status

        async def paper(candidates, quotes):
            handed.append((candidates, quotes))
            return {"status": "idle"}

        deps = BoardDrillDependencies(database=_Database(), run_database=_run, all_a_snapshot=snapshot,
                                      paper_execution=paper, now=lambda: datetime(2026, 10, 13, 2, tzinfo=timezone.utc))
        with mock.patch.object(drill_runtime, "sector_membership", return_value={"881101": {"000001.SZ"}}), \
                mock.patch.object(drill_runtime, "instrument_names", return_value={"000001.SZ": "平安银行"}), \
                mock.patch.object(drill_runtime, "drill_board_events", return_value={
                    "candidates": [{"symbol": "000001.SZ"}], "boards_drilled": 1, "boards_without_membership": 0}):
            result = asyncio.run(drill_board_stock_candidates([{"taxonomy_key": "longhu_ths_industry"}], deps))
        return result, handed

    def test_a_main_source_drill_is_handed_to_paper_execution_as_before(self):
        for status in (FUYAO_STATUS, {}):
            result, handed = self.drill(status)
            self.assertEqual(set(result), {"status", "boards", "boards_drilled", "boards_without_membership",
                                           "candidates", "paper", "decision_eligible"})
            self.assertEqual(result["paper"], {"status": "idle"})
            self.assertEqual(handed[0][0][0]["symbol"], "000001.SZ")
            self.assertEqual(handed[0][1]["000001.SZ"]["price_source"], "fuyao_ths_all_a_snapshot")

    def test_a_fallback_drill_names_its_source_and_is_not_handed_on_while_not_eligible(self):
        result, handed = self.drill(tdx_status(False))
        self.assertEqual(handed, [])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["candidates"][0]["symbol"], "000001.SZ")
        self.assertEqual(result["paper"], {
            "status": "blocked", "reason": SNAPSHOT_NOT_ELIGIBLE_REASON,
            "snapshot_source": "tdx_public_all_a_snapshot", "snapshot_provider": "tdx_public",
            "fallback_for": "fuyao_ths"})
        self.assertEqual((result["snapshot_provider"], result["fallback_for"]), ("tdx_public", "fuyao_ths"))
        self.assertFalse(result["decision_eligible"])

    def test_a_fallback_drill_is_handed_on_once_its_binding_is_eligible(self):
        result, handed = self.drill(tdx_status(True))
        self.assertEqual(result["paper"], {"status": "idle"})
        self.assertEqual(handed[0][1]["000001.SZ"]["price_source"], "tdx_public_all_a_snapshot")
        self.assertEqual(result["snapshot_source"], "tdx_public_all_a_snapshot")


class LimitUpAnchorTests(unittest.TestCase):
    observed = datetime(2026, 10, 13, 2, tzinfo=timezone.utc)
    limits = {"000001.SZ": 10.0}

    def anchors(self, row):
        return live_limit_up_pool_rows([row], self.limits, {"000001.SZ": "平安银行"}, self.observed)

    def test_a_main_source_anchor_is_stored_as_before(self):
        expected = {
            "ts_code": "000001.SZ", "event_type": "limit_up_pool", "published_at": self.observed.isoformat(),
            "title": "limit_up_pool：平安银行", "url": None, "upstream_site": "fuyao_derived",
            "raw": {"price": 10.0, "limit_up": 10.0, "pct_change": 1.2, "high": None, "sealed": True,
                    "source": "fuyao_all_a_plus_stk_limit"},
        }
        unlabelled = {key: value for key, value in FUYAO_ROW.items() if key != "price_source"}
        for row in (FUYAO_ROW, unlabelled):
            self.assertEqual(self.anchors(row), [expected])
            self.assertEqual(snapshot_anchor_source([row]), ("fuyao_derived", "fuyao_all_a_plus_stk_limit"))
        self.assertEqual(snapshot_anchor_source([]), ("fuyao_derived", "fuyao_all_a_plus_stk_limit"))

    def test_a_fallback_anchor_carries_its_own_source(self):
        anchor = self.anchors(TDX_ROW)[0]
        self.assertEqual(anchor["upstream_site"], "tdx_public_derived")
        self.assertEqual(anchor["raw"]["source"], "tdx_public_all_a_plus_stk_limit")
        self.assertEqual(snapshot_anchor_source([TDX_ROW]), ("tdx_public_derived", "tdx_public_all_a_plus_stk_limit"))
        self.assertEqual(anchor_source({"price_source": "some_other_snapshot"}),
                         ("unknown_snapshot_derived", "some_other_snapshot_plus_stk_limit"))

    def refresh(self, row):
        from app import main

        async def context(_day):
            return {"limits": self.limits, "names": {}}

        async def snapshot():
            return [row], {}

        stored = []

        async def blocking(function, *args, **_kwargs):
            stored.append((function.__name__, *args[1:]))
            return 1

        with mock.patch.object(main, "_xiaojie_session_context", context), \
                mock.patch.object(main, "intraday_all_a_snapshot", snapshot), \
                mock.patch.object(main, "run_database_blocking", blocking):
            return asyncio.run(main.refresh_intraday_limit_up_anchors(self.observed)), stored

    def test_the_refresh_stores_and_reports_the_source_the_anchors_came_from(self):
        result, stored = self.refresh(dict(FUYAO_ROW))
        self.assertEqual(result, {"status": "completed", "received": 1, "stored": 1,
                                  "source": "fuyao_all_a_plus_stk_limit"})
        self.assertEqual(stored, [("_persist_local_limit_pool", "fuyao_derived")])
        result, stored = self.refresh(dict(TDX_ROW))
        self.assertEqual(result["source"], "tdx_public_all_a_plus_stk_limit")
        self.assertEqual(stored, [("_persist_local_limit_pool", "tdx_public_derived")])


FUYAO_METADATA = {"status": "fresh", "source": "fuyao_ths_all_a_snapshot", "provider_key": "fuyao_ths",
                  "decision_eligible": True, "cross_sectional": True, "upstream_timestamp_ms": 1, "total": 5580}
MINUTE = datetime(2026, 10, 13, 2, 0, 50, tzinfo=timezone.utc)


def tdx_metadata(eligible=False):
    return {"status": "fresh", "source": "tdx_public_all_a_snapshot", "provider_key": "tdx_public",
            "decision_eligible": eligible, "fallback_for": "fuyao_ths", "fallback_reason": "Fuyao request failed",
            "cross_sectional": True, "upstream_timestamp_ms": None, "total": 5579}


class Level1StorageTests(unittest.TestCase):
    def capture(self, row, metadata, storage="both"):
        written = {}

        async def fetch():
            return [dict(row)], dict(metadata)

        async def persist(provider, capability, rows):
            written["per_symbol"] = (provider, capability, len(rows))
            return len(rows)

        async def persist_document(observed_at, rows, given):
            written["document"] = (observed_at, rows[0]["snapshot_metadata"], dict(given))
            return {"rows": len(rows)}

        async def session_open(_now):
            return True

        with mock.patch.object(level1.minute_cross_section, "remember"):
            result = asyncio.run(capture_level1_snapshot(
                fetch_snapshot=fetch, persist=persist, persist_document=persist_document, session_open=session_open,
                storage=storage, now=MINUTE))
        return result, written

    def test_a_main_source_minute_is_stored_and_reported_as_before(self):
        result, written = self.capture(FUYAO_ROW, FUYAO_METADATA)
        self.assertEqual(written["per_symbol"], ("fuyao_ths", "a_share_prices_snapshot", 1))
        self.assertEqual(written["document"], (MINUTE, FUYAO_METADATA, FUYAO_METADATA))
        self.assertEqual({key: value for key, value in result.items() if key != "timings"}, {
            "status": "completed", "received": 1, "stored": 1, "provider": "fuyao_ths",
            "capability": "a_share_prices_snapshot", "upstream_timestamp_ms": 1, "freshness_status": "fresh",
            "cross_sectional": True, "storage": "both", "document": {"rows": 1}, "document_error": None,
        })
        # Metadata from before snapshots named their provider is the main source's.
        unnamed, written = self.capture(FUYAO_ROW, {"status": "fresh"})
        self.assertEqual((unnamed["provider"], written["per_symbol"][0]), ("fuyao_ths", "fuyao_ths"))

    def test_a_filled_minute_is_stored_and_reported_under_the_provider_that_filled_it(self):
        for eligible in (False, True):
            result, written = self.capture(TDX_ROW, tdx_metadata(eligible))
            self.assertEqual(written["per_symbol"], ("tdx_public", "a_share_prices_snapshot", 1))
            self.assertEqual(written["document"][1]["provider_key"], "tdx_public")
            self.assertEqual((result["provider"], result["capability"]), ("tdx_public", "a_share_prices_snapshot"))
            self.assertEqual((result["fallback_for"], result["fallback_reason"]), ("fuyao_ths", "Fuyao request failed"))
            self.assertIs(result["decision_eligible"], eligible)


class _Transactions:
    @contextmanager
    def transaction(self):
        yield object()


class Level1HealthTests(unittest.TestCase):
    def run_capture(self, row, metadata, storage="document"):
        health, documents = [], []

        async def fetch():
            if row is None:
                raise RuntimeError("fuyao 502; tdx_public fallback: tdx_read_timeout:20s")
            return [dict(row)], dict(metadata)

        async def session_open(_now):
            return True, "open"

        def persist_document(_database, _observed_at, rows, given, *, provider="fuyao_ths", capability=None):
            documents.append((provider, given.get("provider_key")))
            return {"rows": len(rows)}

        deps = Level1CaptureDependencies(
            fetch_snapshot=fetch, persist_observations=lambda _provider, _capability, rows: len(rows),
            run_database=_run, database=_Transactions(), session_open=session_open,
            record_success=lambda _c, provider, capability, rows, _x: health.append(("ok", provider, capability, rows)),
            record_failure=lambda _c, provider, capability, error, _x: health.append(("failed", provider, capability, error)),
            safe_error=lambda text, _n: text,
        )
        with mock.patch.object(level1.minute_cross_section, "storage_mode", return_value=storage), \
                mock.patch.object(level1.minute_cross_section, "persist_document", persist_document), \
                mock.patch.object(level1.minute_cross_section, "remember"):
            try:
                asyncio.run(level1_capture(deps)())
            except RuntimeError:
                pass
        return health, documents

    def test_a_main_source_minute_records_one_success_as_before(self):
        health, documents = self.run_capture(FUYAO_ROW, FUYAO_METADATA)
        self.assertEqual(health, [("ok", "fuyao_ths", "a_share_prices_snapshot", 1)])
        self.assertEqual(documents, [("fuyao_ths", "fuyao_ths")])

    def test_a_failed_minute_records_the_main_failure_as_before(self):
        health, documents = self.run_capture(None, None)
        self.assertEqual(health, [("failed", "fuyao_ths", "a_share_prices_snapshot",
                                   "fuyao 502; tdx_public fallback: tdx_read_timeout:20s")])
        self.assertEqual(documents, [])

    def test_a_filled_minute_records_the_main_failure_with_its_error_and_the_filler_success(self):
        health, documents = self.run_capture(TDX_ROW, tdx_metadata())
        self.assertEqual(health, [
            ("failed", "fuyao_ths", "a_share_prices_snapshot", "Fuyao request failed"),
            ("ok", "tdx_public", "all_a_snapshot", 1),
        ])
        self.assertEqual(documents, [("tdx_public", "tdx_public")])

    def test_the_health_capabilities_are_the_catalog_aliases(self):
        self.assertEqual(health_capability("tdx_public", "quote.all_a_snapshot"), "all_a_snapshot")
        self.assertEqual(level1.snapshot_health_capability("tdx_public"), "all_a_snapshot")
        self.assertEqual(level1.snapshot_health_capability("fuyao_ths"), "a_share_prices_snapshot")
        self.assertEqual(EVIDENCE_CONTRACTS["tdx_public_all_a_snapshot"].capability, "all_a_snapshot")
        self.assertEqual(EVIDENCE_CONTRACTS["fuyao_all_a_snapshot"].capability, "a_share_prices_snapshot")


class MinuteDocumentReaderTests(unittest.TestCase):
    def test_documents_are_read_by_capability_across_providers(self):
        calls = []

        class Connection:
            def execute(self, sql, params=()):
                calls.append((sql, params))
                return mock.MagicMock(fetchone=lambda: None, fetchall=lambda: [])

        class AsyncConnection:
            async def execute(self, sql, params=()):
                calls.append((sql, params))
                return mock.MagicMock(fetchone=mock.AsyncMock(return_value=None))

        day = date(2026, 10, 13)
        minute_cross_section.latest(Connection())
        minute_cross_section.latest(Connection(), day)
        minute_cross_section.first_between(Connection(), MINUTE, MINUTE + timedelta(minutes=1))
        minute_cross_section.day_documents(Connection(), day)
        asyncio.run(minute_cross_section.latest_async(AsyncConnection()))
        self.assertEqual(len(calls), 5)
        for sql, params in calls:
            self.assertIn("provider_key=ANY(%s)", sql)
            self.assertEqual(params[:2], ("a_share_minute_cross_section", ["fuyao_ths", "tdx_public"]))
            self.assertNotIn("a_share_minute_cross_section_shadow", params)
        # Every source of the snapshot chain is among the providers the readers select.
        self.assertEqual(set(minute_cross_section.DOCUMENT_PROVIDERS),
                         {snapshot_sources.FUYAO_PROVIDER_KEY, snapshot_sources.TDX_PROVIDER_KEY})

    def test_the_roster_reads_main_source_documents_only(self):
        calls = []

        class Connection:
            def execute(self, sql, params=()):
                calls.append((sql, params))
                return mock.MagicMock(fetchone=lambda: {"total": 5580})

        self.assertEqual(minute_cross_section.latest_main_total(Connection()), 5580)
        sql, params = calls[0]
        self.assertIn("provider_key=%s", sql)
        self.assertEqual(params, ("a_share_minute_cross_section", "fuyao_ths", "fuyao_ths", "fuyao_ths"))

    def test_the_latest_snapshot_items_name_the_provider_of_the_minute(self):
        def item(row, metadata):
            payload = {**row, "ts_code": row["symbol"], **({"snapshot_metadata": metadata} if metadata else {})}
            return minute_cross_section.latest_level1_items(MINUTE, [payload], 10)[0]["normalized"]

        for metadata in (FUYAO_METADATA, {"status": "fresh"}, None):
            self.assertEqual(item(FUYAO_ROW, metadata)["provider_key"], "fuyao_ths")
        filled = item(TDX_ROW, tdx_metadata())
        self.assertEqual((filled["provider_key"], filled["capability"]), ("tdx_public", "a_share_prices_snapshot"))
        self.assertEqual(filled["price_source"], "tdx_public_all_a_snapshot")

    def test_a_document_defaults_to_the_main_provider_and_takes_the_one_it_is_given(self):
        self.assertEqual(minute_cross_section.snapshot_provider(FUYAO_METADATA), "fuyao_ths")
        self.assertEqual(minute_cross_section.snapshot_provider(None), "fuyao_ths")
        self.assertEqual(minute_cross_section.snapshot_provider(tdx_metadata()), "tdx_public")
        # A minute stored where no reader looks would be a silent gap.
        with self.assertRaises(ValueError):
            minute_cross_section.snapshot_provider({"provider_key": "some_other_source"})


class MinuteDocumentHealthTests(unittest.TestCase):
    def checks(self, fallback_documents):
        summary = {"documents": 240, "max_bytes": 260_000, "last_at": MINUTE, "max_gap_seconds": 61}
        if fallback_documents is not None:
            summary["fallback_documents"] = fallback_documents
        calls = []

        class Connection:
            def execute(self, sql, params=()):
                calls.append(sql)
                return mock.MagicMock(fetchone=lambda: summary)

        checks = {item["name"]: item for item in minute_document_checks(Connection(), date(2026, 10, 13), MINUTE)}
        return checks, calls[0]

    def test_a_day_without_a_filled_minute_is_healthy_as_before(self):
        for count in (0, None):
            checks, sql = self.checks(count)
            self.assertEqual({name: item["status"] for name, item in checks.items()},
                             {"present": "ok", "continuity": "ok", "size": "ok", "fallback_minutes": "ok"})
            self.assertEqual(checks["fallback_minutes"]["value"], 0)
        self.assertNotIn("a_share_minute_cross_section_shadow", sql)
        self.assertNotIn("AND provider_key=", sql)

    def test_filled_minutes_are_counted_and_warn(self):
        checks, _sql = self.checks(3)
        self.assertEqual((checks["fallback_minutes"]["status"], checks["fallback_minutes"]["value"]), ("warn", 3))
        self.assertEqual(checks["present"]["value"], 240)


class ProviderHealthProjectionTests(unittest.TestCase):
    now = datetime(2026, 10, 13, 2, 1, tzinfo=timezone.utc)

    def project(self, sources, health=()):
        payload = project_realtime_source_health(
            {"observed_at": self.now, "items": list(health)},
            {"observed_at": self.now, "summary": {"watched": 2}, "source_status": sources}, session_active=True)
        return {item["source_key"]: item for item in payload["items"]}

    def test_the_filler_has_its_own_entry_under_its_alias(self):
        entry = next(item for item in HEALTH_SOURCES if item[0] == "tdx_public")
        self.assertEqual(entry[1:3], ("tdx_public", health_capability("tdx_public", "quote.all_a_snapshot")))
        self.assertEqual(next(item for item in HEALTH_SOURCES if item[0] == "fuyao_ths")[2], "a_share_prices_snapshot")

    def test_on_a_main_source_scan_the_filler_is_not_tested_and_the_main_source_is_as_before(self):
        snapshot = {"total": 5580, "matched_rows": 5580, "status": "fresh", "age_seconds": 0}
        items = self.project({"fuyao": {"status": "completed", "rows": 5580, "matched": 2, "all_a_snapshot": snapshot}})
        self.assertEqual((items["fuyao_ths"]["state"], items["fuyao_ths"]["requested"], items["fuyao_ths"]["received"]),
                         ("healthy", 5580, 5580))
        self.assertEqual((items["tdx_public"]["state"], items["tdx_public"]["received"]), ("not_tested", None))

    def test_on_a_filled_scan_the_main_source_failed_and_the_filler_served(self):
        snapshot = {**materialize_evidence_status("tdx_public_all_a_snapshot", tdx_status(False)),
                    "fallback_reason": "Fuyao request failed", "flow_decision_eligible": False,
                    "total": 5579, "matched_rows": 5560, "status": "fresh", "age_seconds": 0}
        sources = scan_status({"000001.SZ": {"price_source": "tdx_public_all_a_snapshot"}}, [TDX_ROW], snapshot)
        health = [
            {"provider_key": "fuyao_ths", "capability": "a_share_prices_snapshot", "last_failure_at": self.now,
             "last_error": "Fuyao request failed"},
            {"provider_key": "tdx_public", "capability": "all_a_snapshot", "last_success_at": self.now,
             "last_row_count": 5560},
        ]
        items = self.project(sources, health)
        self.assertEqual(items["fuyao_ths"]["last_observation_state"], "unavailable")
        self.assertEqual(items["fuyao_ths"]["state"], "degraded")
        self.assertTrue(items["fuyao_ths"]["error_present"])
        self.assertEqual((items["tdx_public"]["requested"], items["tdx_public"]["received"]), (5579, 5560))
        self.assertEqual(items["tdx_public"]["last_observation_state"], "partial")
        self.assertEqual(items["tdx_public"]["last_success_at"], self.now)
        self.assertFalse(items["tdx_public"]["decision_eligible"])


if __name__ == "__main__":
    unittest.main()
