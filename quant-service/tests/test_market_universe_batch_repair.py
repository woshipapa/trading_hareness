from datetime import date
from types import SimpleNamespace
import unittest
from app.market_universe_sync import upsert_universe_members

class MarketUniverseBatchTests(unittest.TestCase):
    def test_full_roster_uses_one_statement_and_preserves_existing_priority(self):
        calls = []
        connection = SimpleNamespace(execute=lambda *args: calls.append(args))
        symbols = [f"{i:06d}.SZ" for i in range(5574, 0, -1)]
        upsert_universe_members(connection, "all_a", symbols + symbols[:10],
                                provider="licensed", reference_date=date(2026, 10, 9))
        self.assertEqual(len(calls), 1)
        sql, params = calls[0]
        self.assertIn("unnest(%s::text[])", sql)
        self.assertIn("ORDER BY symbol", sql)
        self.assertNotIn("priority=", sql.split("DO UPDATE SET", 1)[1])
        self.assertEqual(params[2], sorted(set(symbols)))
        self.assertEqual(len(params[2]), 5574)
        self.assertEqual(params[1].obj["reference_date"], "2026-10-09")

