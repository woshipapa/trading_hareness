"""Pure tests for resolver policy and the source quality receipt."""

import unittest
from datetime import datetime, timezone

from app.datasources.contracts import CapabilityEvidence, CapabilityRequest
from app.datasources.resolver import CapabilityResolver, CapabilityUnavailable


class ResolverQualityTests(unittest.IsolatedAsyncioTestCase):
    def _synthetic(self, spec):
        """A test-only capability and binding with ``spec``: production bindings stay untouched."""
        from contextlib import ExitStack
        from unittest import mock

        from app.datasources import resolver as resolver_module
        from app.datasources.contracts import Binding, Capability, CanonicalSchema, DECLARED, FieldSpec

        capability = Capability("test.synthetic", "test", "synthetic", "intraday", "per_symbol", ("price", "volume"),
                                "effective=now", schema=CanonicalSchema((FieldSpec("price", "yuan"), FieldSpec("volume", "shares"))))
        binding = Binding("tdx_public", "test.synthetic", 10, DECLARED, spec=spec)
        stack = ExitStack()
        stack.enter_context(mock.patch.dict(resolver_module.CAPABILITIES, {"test.synthetic": capability}))
        original = resolver_module.bindings_for
        stack.enter_context(mock.patch.object(
            resolver_module, "bindings_for",
            lambda key, states=None: [binding] if key == "test.synthetic" else original(key, **({"states": states} if states else {}))))
        return stack

    async def test_a_spec_maps_fields_and_scales_numbers_only(self):
        from app.datasources.contracts import BindingSpec
        spec = BindingSpec(field_map={"volume_lots": "volume", "price": "price"}, unit_factors={"volume": 100})
        with self._synthetic(spec):
            resolver = CapabilityResolver()

            async def fetch(**_params):
                return [{"price": 2.5, "volume_lots": 3}, {"price": 2.6, "volume_lots": "123"}]

            resolver.bind("tdx_public", "test.synthetic", fetch)
            result = await resolver.fetch("test.synthetic")
        self.assertEqual((result.rows[0]["volume"], type(result.rows[0]["volume"])), (300, int))
        self.assertEqual(result.rows[1]["volume"], "123", "a string is never multiplied")
        self.assertIn("unit_factor_skipped:volume", result.quality.warnings)
        self.assertEqual(result.quality.schema, "canonical")

    async def test_rows_that_are_not_dictionaries_stay_native(self):
        from dataclasses import dataclass

        from app.datasources.contracts import BindingSpec

        @dataclass
        class Tick:
            price: float
            volume_shares: int

        with self._synthetic(BindingSpec(field_map={"price": "price"})):
            resolver = CapabilityResolver()

            async def fetch(**_params):
                return [Tick(2.5, 300)]

            resolver.bind("tdx_public", "test.synthetic", fetch)
            result = await resolver.fetch("test.synthetic")
        self.assertEqual((result.quality.schema, type(result.rows[0]).__name__), ("native", "Tick"))
        self.assertIn("rows_not_normalised", result.quality.warnings)

    async def test_two_native_fields_for_one_canonical_field_are_flagged(self):
        from app.datasources.contracts import BindingSpec
        with self._synthetic(BindingSpec(field_map={"price": "price", "last": "price"})):
            resolver = CapabilityResolver()

            async def fetch(**_params):
                return [{"price": 2.5, "last": 2.6}]

            resolver.bind("tdx_public", "test.synthetic", fetch)
            result = await resolver.fetch("test.synthetic")
        self.assertEqual(result.rows[0]["price"], 2.5)
        self.assertIn("field_map_conflict:price", result.quality.warnings)

    async def test_required_fields_skip_a_spec_binding_before_fetch(self):
        from app.datasources.contracts import BindingSpec
        called = False
        with self._synthetic(BindingSpec(field_map={"price": "price"})):
            resolver = CapabilityResolver()

            async def fetch(**_params):
                nonlocal called
                called = True
                return [{"price": 2.5}]

            resolver.bind("tdx_public", "test.synthetic", fetch)
            with self.assertRaises(CapabilityUnavailable) as raised:
                await resolver.fetch("test.synthetic", request=CapabilityRequest("test.synthetic", required_fields=("volume",)))
        self.assertFalse(called)
        self.assertEqual(raised.exception.attempts[0]["status"], "missing_required_fields")

    async def test_the_real_tdx_ticks_binding_stays_native(self):
        resolver = CapabilityResolver()

        async def fetch(**_params):
            return [{"price": 2.5, "volume_lots": 3, "side": "B", "time": "09:31"}]

        resolver.bind("tdx_public", "ticks.session", fetch)
        result = await resolver.fetch("ticks.session")
        self.assertEqual((result.quality.schema, result.rows[0]["volume_lots"]), ("native", 3))

    async def test_legacy_binding_receipt_is_native(self):
        resolver = CapabilityResolver()

        async def fetch(**_params):
            return [{"native": 1}]

        resolver.bind("eastmoney_ztb", "limits.anomaly_tape", fetch)
        result = await resolver.fetch("limits.anomaly_tape")
        self.assertEqual(result.quality.schema, "native")

    async def test_null_empty_and_nonfinite_required_values_are_missing(self):
        for value in (None, "", float("nan"), float("inf")):
            with self.subTest(value=value):
                resolver = CapabilityResolver()

                async def fetch(measurement=value, **_params):
                    return [{"symbol": "920001.BJ", "pe_ttm": measurement}]

                resolver.bind("fuyao_ths", "quote.valuation", fetch)
                with self.assertRaises(CapabilityUnavailable):
                    await resolver.fetch("quote.valuation", request=CapabilityRequest(
                        "quote.valuation", required_fields=("pe_ttm",)))

    async def test_negative_and_zero_required_values_are_present(self):
        for value in (-41.04, 0):
            with self.subTest(value=value):
                resolver = CapabilityResolver()

                async def fetch(measurement=value, **_params):
                    return [{"symbol": "920001.BJ", "pe_ttm": measurement}]

                resolver.bind("fuyao_ths", "quote.valuation", fetch)
                result = await resolver.fetch("quote.valuation", request=CapabilityRequest(
                    "quote.valuation", required_fields=("pe_ttm",)))
                self.assertEqual(result.quality.status, "complete")

    async def test_missing_required_fields_falls_through(self):
        resolver = CapabilityResolver()

        async def primary(**_params):
            return [{"name": "without a code"}]

        async def backup(**_params):
            return [{"symbol": "000001.SZ", "name": "complete"}]

        resolver.bind("fuyao_ths", "limits.limit_up_pool", primary)
        resolver.bind("eastmoney_ztb", "limits.limit_up_pool", backup)
        result = await resolver.fetch(
            "limits.limit_up_pool",
            request=CapabilityRequest("limits.limit_up_pool", required_fields=("symbol",)),
        )

        self.assertEqual(result.source, "eastmoney_ztb")
        self.assertEqual(result.quality.status, "complete")
        self.assertEqual(result.attempts[0]["status"], "invalid")
        self.assertIn("missing_required_fields:symbol", result.attempts[0]["warnings"])

    async def test_live_and_decision_eligible_request_filters_bindings(self):
        resolver = CapabilityResolver()

        async def longhu(**_params):
            return [{"symbol": "000001.SZ"}]

        async def tencent(**_params):
            return [{"symbol": "000002.SZ"}]

        resolver.bind("longhuvip", "quote.watch_snapshot", longhu)
        resolver.bind("tencent_free", "quote.watch_snapshot", tencent)
        result = await resolver.fetch(
            "quote.watch_snapshot",
            request=CapabilityRequest(
                "quote.watch_snapshot", require_live_verified=True, require_decision_eligible=True,
            ),
        )
        self.assertEqual(result.source, "longhuvip")
        self.assertEqual(result.provenance()["purpose"], "research")

    async def test_coverage_gate_and_response_hash_are_recorded(self):
        resolver = CapabilityResolver()

        async def evidence(**_params):
            return CapabilityEvidence(
                rows=[{"symbol": "000001.SZ"}], coverage=0.5,
                available_at_max=datetime(2026, 9, 18, 7, tzinfo=timezone.utc),
            )

        resolver.bind("eastmoney_ztb", "limits.anomaly_tape", evidence)
        with self.assertRaises(CapabilityUnavailable) as raised:
            await resolver.fetch(
                "limits.anomaly_tape",
                request=CapabilityRequest("limits.anomaly_tape", min_coverage=0.8),
            )
        self.assertEqual(raised.exception.attempts[0]["status"], "partial")
        self.assertTrue(raised.exception.attempts[0]["response_hash"])

        result = await resolver.fetch(
            "limits.anomaly_tape",
            request=CapabilityRequest("limits.anomaly_tape", min_coverage=0.5),
        )
        self.assertEqual(result.quality.coverage, 0.5)
        self.assertEqual(len(result.quality.response_hash), 64)


class CapabilityRequestTests(unittest.TestCase):
    def test_policy_validation(self):
        with self.assertRaises(ValueError):
            CapabilityRequest("quote.watch_snapshot", purpose="live")
        with self.assertRaises(ValueError):
            CapabilityRequest("quote.watch_snapshot", min_coverage=1.1)
        with self.assertRaises(ValueError):
            CapabilityRequest("quote.watch_snapshot", min_rows=-1)


if __name__ == "__main__":
    unittest.main()
