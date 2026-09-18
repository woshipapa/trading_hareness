from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.main import persist_tushare_rows

AVAILABLE_AT = datetime(2026, 9, 18, 7, 0, tzinfo=timezone.utc)


class _Cursor:
    def __init__(self, recorder):
        self._recorder = recorder

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def executemany(self, statement, parameters):
        self._recorder.append(("executemany", statement, list(parameters)))


class _Connection:
    """Records how the evidence rows reach the wire, not what they mean."""

    def __init__(self):
        self.calls: list[tuple] = []

    def cursor(self):
        return _Cursor(self.calls)

    def execute(self, statement, parameters=None):
        self.calls.append(("execute", statement, parameters))
        raise AssertionError("raw evidence must not be written one statement per row")


class TushareRawEvidenceBatchingTests(unittest.TestCase):
    """The owner database is a 52ms round trip away.

    A full-market cross-section is ~5,500 rows, so one statement per row spent
    about five minutes of the 180-second persistence budget waiting on the
    network with the server idle.  These assert the batched shape, which is the
    only part of that cost the transport can remove.
    """

    def setUp(self):
        self.rows = [{"ts_code": "600176.SH", "trade_date": "20260918", "close": "51.0"},
                     {"ts_code": "000001.SZ", "trade_date": "20260918", "close": "12.0"}]
        self.normalized: list[tuple] = []

    def _persist(self, connection, rows):
        # The normalizer is exercised by its own suite; here it only has to be
        # reached with the same arguments the per-row version reached it with.
        import app.main as main

        original = main.normalize_tushare_rows
        main.normalize_tushare_rows = lambda *args, **kwargs: self.normalized.append(args) or len(rows)
        try:
            return persist_tushare_rows(connection, "daily", "req-1", rows, "tushare_primary", AVAILABLE_AT)
        finally:
            main.normalize_tushare_rows = original

    def test_a_cross_section_is_written_in_one_batched_statement(self):
        connection = _Connection()
        self._persist(connection, self.rows)
        self.assertEqual([call[0] for call in connection.calls], ["executemany"])
        self.assertIn("INSERT INTO quant.tushare_raw_records", connection.calls[0][1])

    def test_every_row_keeps_its_own_parameters_and_arrival_order(self):
        connection = _Connection()
        self._persist(connection, self.rows)
        parameters = connection.calls[0][2]
        self.assertEqual(len(parameters), 2)
        self.assertEqual([item[3] for item in parameters], [0, 1])
        self.assertEqual([item[0] for item in parameters], ["tushare_primary", "tushare_primary"])
        self.assertEqual([item[1] for item in parameters], ["daily", "daily"])
        # Distinct content hashes: the conflict key must still separate rows.
        self.assertEqual(len({item[5] for item in parameters}), 2)

    def test_one_statement_per_row_is_kept_so_conflicting_rows_still_resolve(self):
        # A single multi-row VALUES would raise "cannot affect row a second
        # time" when a provider repeats a record key inside one response.
        connection = _Connection()
        self._persist(connection, self.rows + [dict(self.rows[0])])
        self.assertEqual(len(connection.calls[0][2]), 3)

    def test_an_empty_response_still_reaches_the_normalizer_without_writing(self):
        connection = _Connection()
        self._persist(connection, [])
        self.assertEqual(connection.calls, [])
        self.assertEqual(len(self.normalized), 1)


if __name__ == "__main__":
    unittest.main()
