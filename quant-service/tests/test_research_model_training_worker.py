import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from app.research_model_training_worker import (
    FEATURE_NAMES,
    build_training_rows,
    run_training_job,
)


def _source_rows(days: int = 90) -> list[dict]:
    rows = []
    for day_index in range(days):
        exchange_date = date(2026, 1, 1).fromordinal(date(2026, 1, 1).toordinal() + day_index)
        for symbol_index in range(3):
            rows.append({
                "symbol": f"60000{symbol_index}.SH",
                "exchange_date": exchange_date,
                "label": (day_index + symbol_index) % 2,
                "feature_available_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "label_available_at": datetime(2026, 1, 8, tzinfo=timezone.utc),
                **{name: float(day_index + symbol_index + offset) for offset, name in enumerate(FEATURE_NAMES)},
            })
    return rows


class _Result:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((str(sql), params))
        if "replay_readiness_daily_coverage" in str(sql):
            return _Result({"full_days": 720, "first_full_date": date(2023, 1, 1), "last_full_date": date(2026, 1, 1)})
        if "WITH calendar AS" in str(sql):
            return _Result(rows=self.rows)
        if "INSERT INTO quant.research_model_registry" in str(sql):
            return _Result({"model_id": "00000000-0000-0000-0000-000000000001"})
        return _Result()


class _Transaction:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *_args):
        return False


class _Database:
    def __init__(self, rows):
        self.connection = _Connection(rows)

    def transaction(self):
        return _Transaction(self.connection)


class ResearchModelTrainingWorkerTests(unittest.TestCase):
    def test_source_rows_are_converted_to_versioned_export_contract(self):
        output = build_training_rows(_source_rows(1))
        self.assertEqual(len(output), 3)
        self.assertEqual(set(output[0]["features"]), set(FEATURE_NAMES))
        self.assertLessEqual(output[0]["feature_available_at"], output[0]["label_available_at"])

    def test_job_writes_immutable_artifacts_and_only_registers_trained_research_model(self):
        database = _Database(_source_rows())
        with tempfile.TemporaryDirectory() as directory:
            result = run_training_job(
                database,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 4, 1),
                artifact_root=Path(directory),
                trials=(
                    {"trial_key": "small", "l2": 0.01, "learning_rate": 0.05, "iterations": 10},
                    {"trial_key": "large", "l2": 0.1, "learning_rate": 0.05, "iterations": 10},
                ),
            )
        self.assertEqual(result["status"], "trained_research_only")
        self.assertEqual(result["trial_count"], 2)
        self.assertEqual(result["live_effect"], "none")
        self.assertIn(result["registry_status"], {"trained", "rejected"})
        self.assertEqual(len(result["artifact_sha256"]), 64)
        joined = "\n".join(call[0] for call in database.connection.calls)
        self.assertIn("INSERT INTO quant.research_model_registry", joined)
        self.assertIn("INSERT INTO quant.research_model_trials", joined)
        self.assertNotIn("advisory_champion", joined)


if __name__ == "__main__":
    unittest.main()
