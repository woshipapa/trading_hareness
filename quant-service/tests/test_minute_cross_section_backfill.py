"""Converting stored per-symbol minutes into documents, verifying, then deleting (decision 0009, step 2)."""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app import minute_cross_section as mcs
from app import minute_cross_section_backfill as backfill

CN = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 10, 8)
M1, M2 = datetime(2026, 10, 8, 9, 31, tzinfo=CN), datetime(2026, 10, 8, 9, 32, tzinfo=CN)


def stored(minute, count):
    rows = []
    for index in range(count):
        code = f"{600000 + index:06d}.SH"
        rows.append({"symbol": code, "ts_code": code, "price": 10 + index, "pct_change": 1.0 * index,
                     "turnover": 1e6 * index, "snapshot_metadata": {"pages": 2},
                     "provider_key": "fuyao_ths", "capability": "a_share_prices_snapshot", "record_index": index})
    return rows


class FakeDatabase:
    """Holds per-symbol minutes and documents; answers the backfill's statements."""

    def __init__(self):
        self.legacy = {M1: stored(M1, 6), M2: stored(M2, 6)}
        self.documents: dict[datetime, dict] = {}
        self.deletes = 0

    def transaction(self):
        database = self

        class Context:
            def __enter__(self):
                return SimpleNamespace(execute=database.execute)

            def __exit__(self, *exc):
                return False
        return Context()

    def execute(self, sql, params=()):
        if sql.lstrip().startswith("INSERT"):
            self.documents[params[2]] = json.loads(params[5])
            return SimpleNamespace(rowcount=1)
        if sql.lstrip().startswith("DELETE"):
            batch = params[3]
            removed = 0
            for minute in list(self.legacy):
                take = min(batch - removed, len(self.legacy[minute]))
                self.legacy[minute] = self.legacy[minute][take:]
                removed += take
                if not self.legacy[minute]:
                    del self.legacy[minute]
            self.deletes += 1
            return SimpleNamespace(rowcount=removed)
        if "SELECT DISTINCT effective_at" in sql:
            rows = [{"effective_at": minute} for minute in sorted(self.legacy)]
        elif "symbol IS NULL AND effective_at>=%s AND effective_at<%s\"" in sql or (
                "SELECT effective_at FROM" in sql and "symbol IS NULL" in sql):
            rows = [{"effective_at": minute} for minute in self.documents]
        elif "SELECT normalized FROM" in sql:
            rows = [{"normalized": item} for item in reversed(self.legacy.get(params[1], []))]
        elif "symbol IS NULL AND provider_key" in sql:
            minute = params[2]
            document = self.documents.get(minute)
            row = {"effective_at": minute, "normalized": document} if document else None
            return SimpleNamespace(fetchone=lambda: row, fetchall=lambda: [row] if row else [])
        else:
            rows = []
        return SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: rows[0] if rows else None)


class BackfillTests(unittest.TestCase):
    def test_the_safe_window_is_weeknights_and_weekends(self):
        self.assertTrue(backfill.in_safe_window(datetime(2026, 10, 10, 14, 0, tzinfo=CN)))   # Saturday
        self.assertTrue(backfill.in_safe_window(datetime(2026, 10, 9, 23, 0, tzinfo=CN)))
        self.assertFalse(backfill.in_safe_window(datetime(2026, 10, 9, 14, 0, tzinfo=CN)))
        self.assertFalse(backfill.in_safe_window(datetime(2026, 10, 9, 21, 0, tzinfo=CN)))

    def test_convert_is_a_dry_run_until_applied_and_resumes_by_minute(self):
        database = FakeDatabase()
        self.assertEqual(backfill.convert_day(database, DAY, apply=False)["pending"], 2)
        self.assertEqual(database.documents, {})
        result = backfill.convert_day(database, DAY, apply=True)
        self.assertEqual((result["written"], len(database.documents)), (2, 2))
        rebuilt = mcs.rows_of(database.documents[M1])
        self.assertEqual(rebuilt[0]["symbol"], "600000.SH", "capture order restored from record_index")
        self.assertNotIn("record_index", rebuilt[0])
        self.assertEqual(backfill.convert_day(database, DAY, apply=True)["written"], 0, "already documented")

    def test_delete_refuses_a_day_that_does_not_verify_and_batches_when_it_does(self):
        database = FakeDatabase()
        refused = backfill.delete_day(database, DAY, apply=True)
        self.assertIn("refused", refused)
        backfill.convert_day(database, DAY, apply=True)
        self.assertTrue(backfill.verify_day(database, DAY)["ok"])
        pauses = []
        result = backfill.delete_day(database, DAY, apply=True, pause=pauses.append, batch=5)
        self.assertEqual(result["deleted"], 12)
        self.assertEqual(len(pauses), 2, "a pause after each full batch")

    def test_verify_catches_a_document_that_disagrees(self):
        database = FakeDatabase()
        backfill.convert_day(database, DAY, apply=True)
        database.legacy[M2][0]["price"] = 999
        report = backfill.verify_day(database, DAY)
        self.assertFalse(report["ok"])
        self.assertIn("sampled rows differ", report["problems"][0])

    def test_the_command_line_refuses_outside_the_safe_window(self):
        code = backfill.main(["convert", "--from", "2026-10-08", "--to", "2026-10-08"], database=FakeDatabase(),
                             now=datetime(2026, 10, 9, 14, 0, tzinfo=CN))
        self.assertEqual(code, 3)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
