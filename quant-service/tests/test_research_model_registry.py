import unittest

from app.research_model_registry import admission


class ResearchModelRegistryTests(unittest.TestCase):
    def test_incomplete_artifact_is_blocked_and_never_live(self):
        result = admission({"model_key": "demo", "model_version": "v1"})
        self.assertEqual(result["status"], "blocked")
        self.assertIn("artifact_sha256_missing_or_invalid", result["blockers"])
        self.assertEqual(result["live_effect"], "none")

    def test_complete_metadata_is_only_reviewable_not_promoted(self):
        result = admission({
            "model_key": "demo", "model_version": "v1",
            "artifact_sha256": "a" * 64, "data_snapshot_key": "snapshot",
            "feature_contract_version": "features-v1", "label_contract_version": "labels-v1",
            "independent_days": 60, "sample_count": 200,
            "metrics": {"out_of_sample": {"brier": 0.1}},
        })
        self.assertEqual(result["status"], "reviewable")
        self.assertEqual(result["live_effect"], "none")
        self.assertNotIn("promoted", result["status"])
