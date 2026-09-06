import unittest

from app.research_feature_export import export_training_table


class ResearchFeatureExportTests(unittest.TestCase):
    def test_export_is_deterministic_and_binds_contract_versions(self):
        rows = [
            {"symbol": "600000.SH", "exchange_date": "2026-01-02", "features": {"x": 1}, "label": 1},
            {"symbol": "000001.SZ", "exchange_date": "2026-01-02", "features": {"x": 2}, "label": 0},
        ]
        first = export_training_table(rows, feature_names=["x"], feature_contract_version="f-v1", label_contract_version="l-v1")
        second = export_training_table(reversed(rows), feature_names=["x"], feature_contract_version="f-v1", label_contract_version="l-v1")
        self.assertEqual(first["status"], "exported_research_only")
        self.assertEqual(first["data_snapshot_key"], second["data_snapshot_key"])
        self.assertEqual(first["independent_days"], 1)
        self.assertEqual(first["live_effect"], "none")

    def test_export_rejects_duplicate_and_future_available_feature(self):
        row = {
            "symbol": "600000.SH", "exchange_date": "2026-01-02", "features": {"x": 1}, "label": 1,
            "feature_available_at": "2026-01-03T00:00:00+00:00",
            "label_available_at": "2026-01-02T00:00:00+00:00",
        }
        result = export_training_table([row, {**row, "label": 0}], feature_names=["x"], feature_contract_version="f-v1", label_contract_version="l-v1")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("row_0_feature_after_label_availability", result["blockers"])
        self.assertIn("row_1_duplicate_symbol_date", result["blockers"])


if __name__ == "__main__":
    unittest.main()
