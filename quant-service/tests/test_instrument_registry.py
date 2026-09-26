import unittest
from pathlib import Path
import re

from app.instrument_registry import (
    InstrumentRecord,
    ensure_instruments,
    record_from,
    sorted_instrument_records,
)


class InstrumentRegistryTests(unittest.TestCase):
    def test_application_has_one_instrument_write_owner(self):
        app_dir = Path(__file__).parents[1] / "app"
        direct_writers = []
        for path in app_dir.glob("*.py"):
            if path.name == "instrument_registry.py":
                continue
            if re.search(r"insert\s+into\s+quant\.instruments", path.read_text(encoding="utf-8"), re.IGNORECASE):
                direct_writers.append(path.name)
        self.assertEqual(direct_writers, [])

    def test_rows_are_deduplicated_and_sorted_with_first_non_null_metadata(self):
        rows = sorted_instrument_records([
            {"symbol": "600002.SH", "source": "daily"},
            {"symbol": "600001.SH", "name": "A", "source": "basic"},
            {"symbol": "600001.SH", "industry": "I", "source": "daily"},
        ])
        self.assertEqual([row.symbol for row in rows], ["600001.SH", "600002.SH"])
        self.assertEqual(rows[0].name, "A")
        self.assertEqual(rows[0].industry, "I")

    def test_symbol_input_derives_exchange(self):
        row = record_from("000001.SZ", source="offline-import")
        self.assertEqual(row, InstrumentRecord("000001.SZ", "SZ", source="offline-import"))

    def test_invalid_row_is_rejected(self):
        with self.assertRaises(ValueError):
            record_from({"name": "missing"})

    def test_sparse_compatibility_runs_keep_global_symbol_lock_order(self):
        class Connection:
            def __init__(self):
                self.calls = []

            def execute(self, statement, parameters):
                self.calls.append((statement, parameters))

        connection = Connection()
        ensure_instruments(connection, [
            InstrumentRecord("000001.SZ", "SZ", is_st=True),
            InstrumentRecord("000002.SZ", "SZ"),
            InstrumentRecord("000003.SZ", "SZ", is_st=False),
            InstrumentRecord("000004.SZ", "SZ"),
        ], update_existing=True)
        self.assertEqual(
            [call[1][0] for call in connection.calls if "INSERT INTO quant.instruments" in call[0]],
            [["000001.SZ"], ["000002.SZ"], ["000003.SZ"], ["000004.SZ"]],
        )

    def test_batch_statement_reasserts_symbol_order_at_the_sql_boundary(self):
        class Connection:
            def __init__(self):
                self.statement = ""
                self.calls = []

            def execute(self, statement, _parameters):
                self.statement = statement
                self.calls.append((statement, _parameters))

        connection = Connection()
        ensure_instruments(connection, ["600002.SH", "000001.SZ"], source="daily")
        insert_statements = [statement for statement, _parameters in connection.calls
                             if "INSERT INTO quant.instruments" in statement]
        self.assertEqual(len(insert_statements), 1)
        self.assertIn("ORDER BY symbol", insert_statements[0])


if __name__ == "__main__":
    unittest.main()
