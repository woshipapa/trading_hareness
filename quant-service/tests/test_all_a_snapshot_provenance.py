"""Consumers use what the all-A snapshot says about its source (W008 B2).

Each site is pinned twice: with a main-source (Fuyao) snapshot, whose output is what it was before
snapshots named their source, and with a minute the TDX fallback filled. Where eligibility is
involved the fallback is pinned on both sides of its catalog binding's ``decision_eligible``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import unittest
from datetime import datetime, timezone
from unittest import mock

from app import intraday_signal_rules as rules_module
from app.datasources import catalog as catalog_module
from app.datasources.catalog import (
    BINDINGS, EXCHANGE_TIMESTAMPED_QUOTE_LABELS, RULE_USABLE_FLOW_LABELS, SOURCE_LABELS, binding_decision_eligible,
)
from app.datasources.contracts import LIVE_VERIFIED
from app.intraday_derived_flow_metrics import (
    DERIVED_FLOW_LABELS, UNKNOWN_VOLUME_DERIVED_LABEL, apply_derived_watch_flow_metrics, derived_flow_label,
)
from app.intraday_features import annotate_flow_snapshot_provenance
from app.intraday_quote_normalization import observation_source, quote_from_fuyao
from app.intraday_watch_quote_capture import capture_watch_quotes
from app.live_policy import live_policy_gate
from app.main import intraday_signal_rules
from app.platform.evidence_contracts import materialize_evidence_status

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


if __name__ == "__main__":
    unittest.main()
