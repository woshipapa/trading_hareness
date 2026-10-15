from datetime import datetime, timezone
import unittest
import uuid

from app.intraday_rule_snapshot_repository import persist_rule_input_snapshots


class _Cursor:
    def __init__(self, owner):
        self.owner = owner

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def executemany(self, sql, params):
        self.owner.batches.append((sql, list(params)))


class _Connection:
    def __init__(self):
        self.batches = []

    def cursor(self):
        return _Cursor(self)


class IntradayRuleSnapshotRepositoryTests(unittest.TestCase):
    def test_scan_rule_inputs_use_one_cursor_batch(self):
        connection = _Connection()
        scan_id = uuid.uuid4()
        rows = [
            {
                "scan_id": scan_id, "observed_at": datetime(2026, 8, 24, 2, tzinfo=timezone.utc),
                "watch": {"symbol": symbol}, "quote": {"price": 10.0}, "previous_quote": None,
                "daily_factors": {"status": "ready"}, "minute_features": None,
                "peer_context": {}, "model_version": "v1", "market_context": {},
                "fast_confirmation": {"status": "missing"}, "portfolio_context": {},
            }
            for symbol in ("000001.SZ", "600000.SH")
        ]

        hashes = persist_rule_input_snapshots(connection, rows)

        self.assertEqual(len(hashes), 2)
        self.assertEqual(len(connection.batches), 1)
        self.assertIn("ON CONFLICT(scan_id,symbol,model_version) DO NOTHING", connection.batches[0][0])
        self.assertEqual(len(connection.batches[0][1]), 2)
        self.assertEqual([params[1] for params in connection.batches[0][1]], ["000001.SZ", "600000.SH"])


if __name__ == "__main__":
    unittest.main()
