"""The peer's declared hot/cold tiering policy."""

from __future__ import annotations

import unittest

from app.storage_tiering_policy import KEEP_HOT, RULES, tiering_policy, tiering_status


class StorageTieringPolicyTests(unittest.TestCase):
    def test_every_rule_names_a_cold_twin_and_a_positive_hot_window(self):
        for rule in RULES:
            self.assertTrue(rule.cold_twin.endswith("_cold"))
            self.assertGreaterEqual(rule.hot_sessions, 2)          # the overlap always covers the next session
        capabilities = [rule.capability for rule in RULES if rule.capability]
        self.assertEqual(len(capabilities), len(set(capabilities)))
        self.assertFalse(set(capabilities) & set(KEEP_HOT))
        all_a = next(rule for rule in RULES if rule.capability == "a_share_prices_snapshot")
        self.assertTrue(all_a.realtime)
        self.assertEqual(tiering_policy()["mover"], "owner")

    def test_status_reports_tier_usage_and_twin_presence(self):
        class Result:
            def __init__(self, rows):
                self.rows = rows

            def fetchall(self):
                return self.rows

        class Connection:
            def execute(self, sql, params=None):
                if "GROUP BY 1" in sql:
                    return Result([{"tablespace": "pg_default", "bytes": 100}, {"tablespace": "stock_cold", "bytes": 7}])
                return Result([{"name": "quant.raw_market_observations_cold", "tablespace": "stock_cold"}])

        status = tiering_status(Connection())
        self.assertEqual(status["tier_usage_bytes"], {"pg_default": 100, "stock_cold": 7})
        self.assertTrue(status["cold_twins"]["quant.raw_market_observations_cold"]["exists"])
        self.assertFalse(status["cold_twins"]["quant.intraday_quote_observations_cold"]["exists"])


if __name__ == "__main__":
    unittest.main()
