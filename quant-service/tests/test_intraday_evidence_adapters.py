from __future__ import annotations

import unittest

from app.intraday_evidence_adapters import (
    insert_board_flow_snapshots,
    insert_minute_sessions,
    insert_quote_observations,
)


class _Cursor:
    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def executemany(self, sql, rows):
        self.calls.append((sql, list(rows)))


class _Database:
    def __init__(self):
        self.calls = []

    def cursor(self):
        return _Cursor(self.calls)


class IntradayEvidenceAdapterTests(unittest.TestCase):
    def test_quote_adapter_preserves_scan_and_source_keys(self):
        database = _Database()
        count = insert_quote_observations(database, [{
            "symbol": "000001.SZ", "capability": "longhu_order_book",
            "effective_at": "2026-10-16T07:30:00+00:00", "payload": {
                "scan_id": "scan-1", "symbol": "000001.SZ", "observed_at": "2026-10-16T07:30:00+00:00",
                "source_name": "longhu_order_book", "price": 10.2, "raw": {"bid": 10.1},
            },
        }])
        self.assertEqual(count, 1)
        sql, rows = database.calls[0]
        self.assertIn("intraday_quote_observations", sql)
        self.assertEqual(rows[0][0:4], ("scan-1", "000001.SZ", "2026-10-16T07:30:00+00:00", "longhu_order_book"))

    def test_board_adapter_uses_snapshot_minute_upsert(self):
        database = _Database()
        count = insert_board_flow_snapshots(database, [{
            "symbol": "board:flow", "captured_at": "2026-10-16T07:30:00+00:00",
            "effective_at": "2026-10-16T07:30:00+00:00", "payload": {
                "snapshot_minute": "2026-10-16T15:00:00+08:00", "coverage": {"boards": 3},
                "source_status": {"provider": "eastmoney"}, "payload": {"top": []},
            },
        }])
        self.assertEqual(count, 1)
        self.assertIn("intraday_board_flow_snapshots", database.calls[0][0])

    def test_minute_adapter_expands_rows_into_real_session_table(self):
        database = _Database()
        count = insert_minute_sessions(database, [{
            "symbol": "000001.SZ", "capability": "longhu_intraday_minutes",
            "effective_at": "2026-10-16T07:30:00+00:00", "available_at": "2026-10-16T07:30:01+00:00",
            "payload": {"rows": [{
                "symbol": "000001.SZ", "trading_date": "2026-10-16", "minute_bucket": "09:31",
                "bar_time": "2026-10-16T09:31:00+08:00", "close": 10.2, "source_name": "longhu_intraday_minutes",
            }]},
        }])
        self.assertEqual(count, 1)
        self.assertIn("intraday_minute_sessions", database.calls[0][0])


if __name__ == "__main__":
    unittest.main()
