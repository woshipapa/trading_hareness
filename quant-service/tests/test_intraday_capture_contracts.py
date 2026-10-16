from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

from app.intraday_capture_contracts import CaptureBatch, EvidenceRecord


class IntradayCaptureContractTests(unittest.TestCase):
    def test_batch_freezes_rows_and_keeps_point_in_time_header(self):
        rows = [{"symbol": "000001.SZ", "price": 10}]
        batch = CaptureBatch.build(
            provider_key="longhuvip", capability="order_book_quote",
            observed_at=datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc),
            available_at=None, rows=rows, batch_id=uuid.UUID(int=1), sequence=7,
        )
        rows[0]["price"] = 99
        self.assertEqual(batch.rows[0]["price"], 10)
        self.assertEqual(batch.trade_date.isoformat(), "2026-10-16")
        self.assertEqual(batch.header()["row_count"], 1)
        self.assertEqual(len(batch.payload_sha256), 64)

    def test_record_has_fixed_stream_and_deterministic_id(self):
        batch = CaptureBatch.build(
            provider_key="fuyao_ths", capability="a_share_prices_snapshot",
            observed_at=datetime(2026, 10, 16, 5, 0, tzinfo=timezone.utc),
            available_at=None, rows=[{"symbol": "000001.SZ"}], batch_id=uuid.UUID(int=2),
        )
        record = EvidenceRecord.from_batch(
            batch, target_stream="raw_market_observations", sequence=3,
            effective_at=batch.observed_at, normalized={"status": "complete"},
            payload={"symbol": "000001.SZ", "price": 10},
        )
        again = EvidenceRecord.from_batch(
            batch, target_stream="raw_market_observations", sequence=3,
            effective_at=batch.observed_at, normalized={"status": "complete"},
            payload={"symbol": "000001.SZ", "price": 10},
        )
        self.assertEqual(record.record_id, again.record_id)
        self.assertEqual(record.as_dict()["target_stream"], "raw_market_observations")
        with self.assertRaises(ValueError):
            EvidenceRecord.from_batch(
                batch, target_stream="raw market", sequence=0,
                effective_at=batch.observed_at, normalized={}, payload={},
            )


if __name__ == "__main__":
    unittest.main()
