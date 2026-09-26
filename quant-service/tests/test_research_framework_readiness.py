import unittest

from app.research_framework_readiness import enrich_framework_rows, framework_readiness


class ResearchFrameworkReadinessTests(unittest.TestCase):
    def test_native_framework_is_available_but_only_native_baseline(self):
        result = framework_readiness({"framework_key": "native_factor_lab", "status": "native"})
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["benchmark_status"], "native_baseline_only")
        self.assertEqual(result["blocked_reasons"], [])
        self.assertTrue(result["research_only"])
        self.assertEqual(result["live_effect"], "none")

    def test_adapter_ready_framework_stays_blocked_when_package_and_data_are_missing(self):
        result = framework_readiness(
            {"framework_key": "qlib", "status": "adapter_ready"},
            package_probe=lambda _package: False,
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["benchmark_status"], "not_executed")
        self.assertIn("cross_framework_benchmark_not_executed", result["blocked_reasons"])
        self.assertIn("optional_dependency_unavailable", result["blocked_reasons"])
        self.assertIn("data_gates_not_evaluated", result["blocked_reasons"])
        self.assertEqual(result["data_gate_status"]["point_in_time_history_3y"], "not_evaluated")
        self.assertEqual(result["runtime_dependency"]["state"], "missing")
        self.assertEqual(result["live_effect"], "none")

    def test_dependency_presence_does_not_promote_external_benchmark(self):
        result = framework_readiness(
            {"framework_key": "lean", "status": "adapter_ready"},
            package_probe=lambda package: package == "lean",
        )
        self.assertEqual(result["runtime_dependency"]["state"], "available")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("cross_framework_benchmark_not_executed", result["blocked_reasons"])
        self.assertIn("data_gates_not_evaluated", result["blocked_reasons"])
        self.assertEqual(result["data_gate_status"]["a_share_transaction_model"], "not_evaluated")

    def test_planned_and_unknown_catalog_statuses_remain_explicit(self):
        planned = framework_readiness({"framework_key": "finrl", "status": "planned"})
        unknown = framework_readiness({"framework_key": "new", "status": "future"})
        self.assertEqual(planned["status"], "planned")
        self.assertEqual(planned["blocked_reasons"], ["adapter_not_implemented"])
        self.assertEqual(unknown["status"], "unknown")
        self.assertEqual(unknown["blocked_reasons"], ["catalog_status_unrecognized"])

    def test_enrichment_preserves_catalog_fields_and_adds_readiness(self):
        rows = enrich_framework_rows(
            [{"framework_key": "qlib", "status": "adapter_ready", "label": "Qlib"}],
            package_probe=lambda _package: False,
        )
        self.assertEqual(rows[0]["label"], "Qlib")
        self.assertEqual(rows[0]["readiness"]["status"], "blocked")
if __name__ == "__main__":
    unittest.main()
