from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
import unittest

from app.xiaojie_reference_repository import persist_trade_limit_rows


class _Connection:
    def __init__(self):
        self.batches = []
        self.single = []

    @contextmanager
    def cursor(self):
        connection = self

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def executemany(self, statement, parameters):
                connection.batches.append((statement, list(parameters)))

        yield Cursor()

    def execute(self, statement, parameters=None):
        self.single.append((statement, parameters))


class XiaojieReferenceBatchingTests(unittest.TestCase):
    def test_limit_cross_section_uses_one_batch(self):
        connection = _Connection()
        rows = [
            {"ts_code": "600001.SH", "up_limit": "11", "down_limit": "9"},
            {"ts_code": "000001.SZ", "up_limit": "13", "down_limit": "11"},
        ]
        stored = persist_trade_limit_rows(
            connection, date(2026, 10, 15), rows, "tencent_free", datetime(2026, 10, 15, tzinfo=timezone.utc),
        )
        self.assertEqual(stored, 2)
        self.assertEqual(len(connection.batches), 1)
        self.assertEqual(len(connection.batches[0][1]), 2)
        self.assertEqual(connection.single, [])


if __name__ == "__main__":
    unittest.main()
