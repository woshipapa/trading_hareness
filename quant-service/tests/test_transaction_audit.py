"""Every transaction's duration and statement count, grouped by who opened it."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import unittest
from unittest.mock import patch

from app.transaction_audit import TransactionAudit, slow_transaction_seconds, transaction_site


class TransactionAuditTests(unittest.TestCase):
    def test_only_slow_transactions_count_as_slow_but_every_one_is_seen(self):
        audit = TransactionAudit()
        self.assertFalse(audit.record("app.x:fast", 0.2, 3, slow_after=5))
        self.assertTrue(audit.record("app.x:slow", 7.0, 250, slow_after=5))
        summary = audit.summary()
        self.assertEqual(summary["observed_sites"], 2)
        self.assertEqual(summary["slow_total"], 1)
        self.assertEqual([item["site"] for item in summary["slowest_sites"]], ["app.x:slow"])
        self.assertEqual(summary["slowest_sites"][0]["max_statements"], 250)
        self.assertEqual(summary["recent_slow"][0]["seconds"], 7.0)

    def test_sites_rank_by_how_often_they_are_slow(self):
        audit = TransactionAudit()
        audit.record("app.a:once", 60.0, 1, slow_after=1)
        for _ in range(3):
            audit.record("app.b:often", 2.0, 400, slow_after=1)
        self.assertEqual([item["site"] for item in audit.summary()["slowest_sites"]], ["app.b:often", "app.a:once"])

    def test_concurrent_records_are_not_lost(self):
        audit = TransactionAudit()
        threads = [threading.Thread(target=lambda: [audit.record("app.t:x", 0.01, 1, slow_after=9) for _ in range(500)])
                   for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(audit._sites["app.t:x"].transactions, 4000)

    def test_the_threshold_comes_from_the_environment_and_ignores_garbage(self):
        with patch.dict(os.environ, {"QUANT_SLOW_TRANSACTION_SECONDS": "2.5"}):
            self.assertEqual(slow_transaction_seconds(), 2.5)
        with patch.dict(os.environ, {"QUANT_SLOW_TRANSACTION_SECONDS": "soon"}):
            self.assertEqual(slow_transaction_seconds(), 5.0)

    def test_site_is_the_module_and_function(self):
        self.assertEqual(transaction_site(sys._getframe(0)), f"{__name__}:test_site_is_the_module_and_function")


@unittest.skipUnless(os.getenv("PGHOST"), "requires the compose PostgreSQL service")
class DatabaseTransactionAuditTests(unittest.TestCase):
    """The real Database and AsyncDatabase attribute each transaction to the function that opened it."""

    def test_sync_transactions_are_recorded_against_their_caller(self):
        from app.database import Database
        from app.transaction_audit import TRANSACTION_AUDIT
        database = Database()
        try:
            TRANSACTION_AUDIT.reset()
            with patch.dict(os.environ, {"QUANT_SLOW_TRANSACTION_SECONDS": "0.1"}):
                with database.transaction() as connection:
                    connection.execute("SELECT 1")
                    connection.execute("SELECT pg_sleep(0.2)")
            summary = TRANSACTION_AUDIT.summary()
            site = f"{__name__}:test_sync_transactions_are_recorded_against_their_caller"
            self.assertEqual(summary["slowest_sites"][0]["site"], site)
            self.assertEqual(summary["slowest_sites"][0]["max_statements"], 2)
            self.assertIn("transactions", database.pool_status())
        finally:
            database.close()
            TRANSACTION_AUDIT.reset()

    def test_async_transactions_are_recorded_against_their_caller(self):
        from app.database import AsyncDatabase
        from app.transaction_audit import TRANSACTION_AUDIT

        async def opener():
            database = AsyncDatabase()
            try:
                async with database.transaction() as connection:
                    await connection.execute("SELECT pg_sleep(0.2)")
            finally:
                await database.close()

        TRANSACTION_AUDIT.reset()
        try:
            with patch.dict(os.environ, {"QUANT_SLOW_TRANSACTION_SECONDS": "0.1"}):
                asyncio.run(opener())
            sites = [item["site"] for item in TRANSACTION_AUDIT.summary()["slowest_sites"]]
            self.assertEqual(sites, [f"async:{__name__}:opener"])
        finally:
            TRANSACTION_AUDIT.reset()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
