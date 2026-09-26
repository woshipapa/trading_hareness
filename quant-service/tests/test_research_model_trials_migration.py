from pathlib import Path
import unittest


class ResearchModelTrialsMigrationTests(unittest.TestCase):
    def test_trials_are_append_only_and_tied_to_registered_model(self):
        source = (Path(__file__).parents[1] / "migrations" / "versions" / "20260919_ds0003_research_model_trials.py").read_text()
        self.assertIn('revision = "20260919_ds0003"', source)
        self.assertIn('down_revision = "20260919_ds0002"', source)
        self.assertIn("REFERENCES quant.research_model_registry", source)
        self.assertIn("UNIQUE(model_id,trial_key)", source)
        self.assertIn("live_effect", source)


if __name__ == "__main__":
    unittest.main()
