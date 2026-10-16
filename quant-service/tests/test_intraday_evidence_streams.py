from __future__ import annotations

import unittest

from app.intraday_evidence_streams import stream_contract


class IntradayEvidenceStreamTests(unittest.TestCase):
    def test_stream_contract_has_fixed_target_and_key(self):
        contract = stream_contract("raw_market_observations")
        self.assertEqual(contract.target_table, "quant.raw_market_observations")
        self.assertIn("payload_sha256", contract.idempotency_key)
        self.assertEqual(contract.status, "implemented")

    def test_unknown_stream_fails_closed(self):
        with self.assertRaises(ValueError):
            stream_contract("arbitrary_sql")

    def test_minute_stream_matches_the_real_session_table(self):
        contract = stream_contract("intraday_minute_sessions")
        self.assertEqual(contract.target_table, "quant.intraday_minute_sessions")
        self.assertEqual(contract.idempotency_key, ("symbol", "trading_date", "minute_bucket", "source_name"))


if __name__ == "__main__":
    unittest.main()
