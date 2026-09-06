from pathlib import Path
import unittest


class ResearchModelMigrationTests(unittest.TestCase):
    def test_model_registry_requires_artifact_and_research_lineage_fields(self):
        source = (Path(__file__).parents[1] / "migrations" / "versions" / "20260906_0094_research_model_registry.py").read_text()
        self.assertIn('revision = "20260906_0094"', source)
        self.assertIn('down_revision = "20260905_0093"', source)
        self.assertIn("artifact_sha256", source)
        self.assertIn("data_snapshot_key", source)
        self.assertIn("advisory_champion", source)
        self.assertIn("research_model_registry_status_idx", source)


if __name__ == "__main__":
    unittest.main()
