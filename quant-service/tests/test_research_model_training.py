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
        self.assertEqual(result["parameters"]["l2"], 0.01)
        self.assertIn("roc_auc", result["metrics"])
        self.assertIn("constant_baseline", result["metrics"])
        self.assertEqual(result["live_effect"], "none")

    def test_trial_hyperparameters_are_audited_and_deterministic(self):
        rows = []
        start = date(2026, 1, 2)
        for index in range(85):
            day = str(start + timedelta(days=index))
            rows.extend([
                {"exchange_date": day, "label": index % 2, "x": float(index % 7)},
                {"exchange_date": day, "label": (index + 1) % 2, "x": float((index + 2) % 7)},
                {"exchange_date": day, "label": index % 2, "x": float((index + 4) % 7)},
            ])
        first = train_oof(rows, feature_names=["x"], l2=0.1, learning_rate=0.05, iterations=20)
        second = train_oof(rows, feature_names=["x"], l2=0.1, learning_rate=0.05, iterations=20)
        self.assertEqual(first["artifact"], second["artifact"])
        self.assertEqual(first["metrics"], second["metrics"])
        self.assertEqual(first["parameters"], {"l2": 0.1, "learning_rate": 0.05, "iterations": 20})


if __name__ == "__main__":
    unittest.main()
