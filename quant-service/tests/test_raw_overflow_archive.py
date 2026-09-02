import os
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import UUID

from app.raw_overflow_archive import RawOverflowConfig, next_batch, stream_key


class _Connection:
    def __init__(self):
        self.rows = [
            {
                "observation_id": UUID("00000000-0000-0000-0000-000000000001"),
                "provider_key": "tencent_free", "capability": "realtime_quote", "market": "cn",
                "symbol": "000001.SZ", "effective_at": datetime(2026, 8, 31, 1, 0, tzinfo=timezone.utc),
                "available_at": datetime(2026, 8, 31, 1, 0, tzinfo=timezone.utc), "ingested_at": None,
                "availability_basis": "provider_time", "payload_sha256": "a" * 64,
                "normalized": {"close": 10}, "payload": {"close": 10}, "fetch_run_id": None,
                "created_at": datetime(2026, 8, 31, 1, 0, tzinfo=timezone.utc),
            },
        ]

    def execute(self, sql, params=None):
        if "pg_total_relation_size" in sql:
            return _Result({"bytes": 900 * 1024 * 1024})
        if "count(*)::int AS count" in sql:
            return _Result({"count": 0})
        if "SELECT effective_at,observation_id FROM quant.raw_archive_offsets" in sql:
            return _Result({})
        if "SELECT observation_id,provider_key,capability" in sql:
            return _Result(self.rows)
        return _Result({})


class _Result:
    def __init__(self, rows):
        self.rows = rows if isinstance(rows, list) else [rows]
        self.rowcount = 0

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _Database:
    @contextmanager
    def transaction(self):
        yield _Connection()


class RawOverflowArchiveTests(unittest.TestCase):
    def test_next_batch_is_keyset_bounded_and_token_free(self):
        old = os.environ.get("QUANT_HOT_DATABASE_SOFT_BYTES")
        os.environ["QUANT_HOT_DATABASE_SOFT_BYTES"] = str(1024 * 1024 * 1024)
        try:
            config = RawOverflowConfig(enabled=True, capabilities=("realtime_quote",), batch_rows=1)
            result = next_batch(_Database(), stream=stream_key("realtime_quote"), limit=100, config=config)
        finally:
            if old is None:
                os.environ.pop("QUANT_HOT_DATABASE_SOFT_BYTES", None)
            else:
                os.environ["QUANT_HOT_DATABASE_SOFT_BYTES"] = old
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(result["first_offset"]["observation_id"], "00000000-0000-0000-0000-000000000001")
        self.assertNotIn("access_token", repr(result))


if __name__ == "__main__":
    unittest.main()
