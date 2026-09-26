"""Pure tests for resolver policy and the source quality receipt."""

import unittest
from datetime import datetime, timezone

from app.datasources.contracts import CapabilityEvidence, CapabilityRequest
from app.datasources.resolver import CapabilityResolver, CapabilityUnavailable


class ResolverQualityTests(unittest.IsolatedAsyncioTestCase):
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
