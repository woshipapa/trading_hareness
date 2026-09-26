"""Hot -> cold copy with overlap and verified deletion; inert without the owner's grant."""

from __future__ import annotations

import unittest
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app import storage_tiering_mover as mover_module
from app.storage_tiering_mover import StorageTieringMover, rule_readiness, transfer_allowed
from app.storage_tiering_policy import TierRule

CN = ZoneInfo("Asia/Shanghai")
RULE = TierRule("quant.raw_market_observations", "quant.raw_market_observations_cold", "effective_at",
                "a_share_prices_snapshot", 2, True, "test")
COLUMNS = ["observation_id", "provider_key", "capability", "symbol", "effective_at", "payload_sha256", "payload"]


class Result:
    def __init__(self, rows, rowcount=0):
        self.rows, self.rowcount = rows, rowcount

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeDatabase:
    def __init__(self, *, granted=True, key_index=True):
        self.granted, self.key_index, self.statements = granted, key_index, []

    def transaction(self):
        database = self

        class Tx:
            def __enter__(self):
                return database

            def __exit__(self, *exc):
                return False
        return Tx()

    def execute(self, sql, params=()):
        if sql.startswith("SET LOCAL"):
            return Result([])
        if "calendar_date=%s" in sql:
            return Result([{"1": 1}])                                      # today is a trading day
        if "calendar_date<%s" in sql:
            return Result([{"calendar_date": date(2026, 9, d)} for d in (21, 18, 17)])
        if "to_regclass" in sql:
            return Result([{"cold_exists": True, "cold_ok": self.granted, "hot_ok": True}])
        if "information_schema.columns" in sql:
            return Result([{"column_name": c} for c in COLUMNS])
        if "pg_indexes" in sql:
            return Result([{"indexdef": "CREATE UNIQUE INDEX x ON quant.raw_market_observations_cold USING btree (observation_id)"}]
                          if self.key_index else [])
        if "WITH RECURSIVE" in sql:
            return Result([{"symbol": "000001.SZ"}, {"symbol": "000002.SZ"}])
        if "symbol IS NULL" in sql and "LIMIT 1" in sql:
            return Result([])
        if "min(x.m)" in sql:
            return Result([{"oldest": datetime(2026, 9, 17, 9, 16, tzinfo=CN)}])
        if sql.startswith("INSERT INTO quant.raw_market_observations_cold") or sql.startswith("DELETE FROM"):
            self.statements.append((sql.split()[0], params[-2].date()))
            return Result([], rowcount=10)
        raise AssertionError(f"unexpected SQL: {sql[:80]}")


class StorageTieringMoverTests(unittest.TestCase):
    def test_never_inside_the_trading_session(self):
        self.assertFalse(transfer_allowed(datetime(2026, 9, 22, 13, 0, tzinfo=CN), True))
        self.assertFalse(transfer_allowed(datetime(2026, 9, 22, 15, 30, tzinfo=CN), True))
        self.assertTrue(transfer_allowed(datetime(2026, 9, 22, 15, 45, tzinfo=CN), True))
        self.assertTrue(transfer_allowed(datetime(2026, 9, 26, 13, 0, tzinfo=CN), False))    # a non-trading day

    def test_inert_until_the_owner_grants_and_the_twin_has_a_key_index(self):
        self.assertEqual(rule_readiness(FakeDatabase(granted=False), RULE)["status"], "awaiting_owner_grant")
        self.assertEqual(rule_readiness(FakeDatabase(key_index=False), RULE)["status"], "cold_twin_needs_key_index")
        database = FakeDatabase(granted=False)
        with patch.object(mover_module, "RULES", (RULE,)):
            report = StorageTieringMover(database, now=lambda: datetime(2026, 9, 22, 20, 0, tzinfo=CN)).run_pass()
        self.assertEqual(report["status"], "awaiting_owner_grant")
        self.assertEqual(database.statements, [])

    def test_copies_every_closed_day_and_deletes_only_past_the_hot_window(self):
        database = FakeDatabase()
        with patch.object(mover_module, "RULES", (RULE,)):
            mover = StorageTieringMover(database, now=lambda: datetime(2026, 9, 22, 20, 0, tzinfo=CN))
            report = mover.run_pass()
            copied_days = sorted({day for kind, day in database.statements if kind == "INSERT"})
            deleted_days = sorted({day for kind, day in database.statements if kind == "DELETE"})
            # hot_sessions=2 keeps today and 09-21; older sessions are pruned after their copy
            self.assertEqual(copied_days, [date(2026, 9, d) for d in (17, 18, 19, 20, 21)])
            self.assertEqual(deleted_days, [date(2026, 9, d) for d in (17, 18, 19, 20)])
            for day in deleted_days:                       # each day is copied before it is pruned
                first_copy = database.statements.index(("INSERT", day))
                self.assertLess(first_copy, database.statements.index(("DELETE", day)))
            self.assertEqual(report["status"], "completed")
            database.statements.clear()
            mover.run_pass()                               # a copied day is not re-copied in the same process
            self.assertEqual({kind for kind, _ in database.statements}, {"DELETE"})

    def test_the_delete_verifies_key_and_payload_hash(self):
        sql = mover_module._delete_sql(RULE, mover_module.TABLES[RULE.table], "h.capability=%s")
        self.assertIn("c.observation_id=h.observation_id", sql)
        self.assertIn("c.payload_sha256 IS NOT DISTINCT FROM h.payload_sha256", sql)
        copy = mover_module._copy_sql(RULE, COLUMNS, mover_module.TABLES[RULE.table], "h.capability=%s")
        self.assertIn("NOT EXISTS", copy)


if __name__ == "__main__":
    unittest.main()
