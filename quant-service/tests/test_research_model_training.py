import unittest
from datetime import date, timedelta

from app.research_model_training import train_oof


class ResearchModelTrainingTests(unittest.TestCase):
    def test_training_fails_closed_below_independent_sample_gate(self):
        result = train_oof([{"exchange_date": "2026-01-01", "label": 1, "x": 1}], feature_names=["x"])
        self.assertEqual(result["status"], "blocked")
        self.assertIn("less_than_60_independent_days", result["blockers"])
        self.assertEqual(result["live_effect"], "none")

    def test_training_uses_chronological_oof_with_embargo(self):
        rows = []
        start = date(2026, 1, 2)
        for index in range(85):
            day = str(start + timedelta(days=index))
            rows.extend([{"exchange_date": day, "label": index % 2, "x": float(index)},
                         {"exchange_date": day, "label": (index + 1) % 2, "x": float(index + 1)},
                         {"exchange_date": day, "label": index % 2, "x": float(index + 2)}])
        result = train_oof(rows, feature_names=["x"])
        self.assertEqual(result["status"], "trained_research_only")
        self.assertEqual(result["folds"], 1)
        self.assertEqual(result["oof_days"], 20)
        self.assertTrue(result["metrics"]["out_of_sample"])
        self.assertEqual(result["live_effect"], "none")


if __name__ == "__main__":
    unittest.main()
