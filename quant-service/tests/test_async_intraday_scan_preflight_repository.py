from __future__ import annotations

import unittest

from app.async_intraday_scan_preflight_repository import latest_board_report


class _Result:
    def __init__(self, *, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self):
        self.calls = []

    async def execute(self, query, params=None):
        self.calls.append((query, params))
        if "intraday_board_reports" in query:
            return _Result(row={"observed_at": "2026-08-22T07:00:00+00:00", "status": "completed"})
        return _Result(rows=[{"symbol": "000001.SZ", "price": 10.0}])


class _Transaction:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_):
        return False


class _Database:
    def __init__(self):
        self.connection = _Connection()

    def transaction(self):
        return _Transaction(self.connection)


class AsyncIntradayScanPreflightRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_board_report_query_is_native_async(self) -> None:
        database = _Database()
        board = await latest_board_report(database)

        self.assertEqual(board["status"], "completed")
        board_query, board_params = database.connection.calls[0]
        self.assertIn("intraday_board_reports", board_query)
        self.assertIsNone(board_params)


if __name__ == "__main__":
    unittest.main()
