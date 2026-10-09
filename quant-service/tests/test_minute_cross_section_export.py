"""The session's minutes as a Parquet research panel, and the bounded panel read (decision 0009, step 3)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app import minute_cross_section as mcs
from app.minute_cross_section_export import export_day, panel, panel_path

CN = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 10, 12)
MINUTES = [datetime(2026, 10, 12, 9, 31, tzinfo=CN) + timedelta(minutes=offset) for offset in range(3)]


def minute_rows(offset):
    return [{"symbol": code, "ts_code": code, "price": 10.0 + offset + index, "pct_change": 1.0 + offset,
             "turnover": 1e6 * (offset + 1), "volume": 1e4,
             "raw": {"open_price": 10.0 + index, "high_price": 11.0 + index, "low_price": 9.0 + index, "prev_price": 9.5}}
            for index, code in enumerate(("600001.SH", "000001.SZ"))]


class _Database:
    def __init__(self, documents):
        self.documents = documents

    def transaction(self):
        database = self

        class Context:
            def __enter__(self):
                rows = [{"effective_at": minute, "normalized": document} for minute, document in database.documents]
                return SimpleNamespace(execute=lambda sql, params=(): SimpleNamespace(fetchall=lambda: rows))

            def __exit__(self, *exc):
                return False
        return Context()


def database():
    return _Database([(minute, mcs.build_document(minute_rows(offset), minute))
                      for offset, minute in enumerate(MINUTES)])


class ExportTests(unittest.TestCase):
    def test_a_session_exports_to_one_parquet_file_and_reads_back_by_symbol(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = export_day(database(), DAY, root=root)
            self.assertEqual((result["status"], result["minutes"], result["rows"]), ("completed", 3, 6))
            self.assertTrue(panel_path(DAY, root).exists())
            read = panel(_Database([]), DAY, ["600001.SH"], ["price", "open"], root=root)
            self.assertEqual(read["source"], "parquet")
            self.assertEqual(read["series"]["600001.SH"]["price"], [10.0, 11.0, 12.0])
            self.assertEqual(read["series"]["600001.SH"]["open"], [10.0, 10.0, 10.0])

    def test_a_session_without_a_file_reads_its_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            read = panel(database(), DAY, ["000001.SZ", "999999.SH"], ["pct_change"], root=Path(directory))
        self.assertEqual(read["source"], "documents")
        self.assertEqual(read["series"]["000001.SZ"]["pct_change"], [1.0, 2.0, 3.0])
        self.assertEqual(read["series"]["999999.SH"]["pct_change"], [])

    def test_a_session_stored_only_per_symbol_is_missing_not_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(export_day(_Database([]), DAY, root=Path(directory))["status"], "missing")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
