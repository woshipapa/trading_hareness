from __future__ import annotations

import os
import unittest
import uuid

from app.async_runtime_lease_repository import acquire as acquire_async
from app.database import AsyncDatabase, Database
from app.runtime_leases import acquire_runtime_lease, runtime_lease_holder_id


class RuntimeLeaseIdentityTests(unittest.TestCase):
    def test_identity_is_stable_for_hostname_and_pid(self) -> None:
        holder = runtime_lease_holder_id("quant-container", 1)

        self.assertEqual(holder, runtime_lease_holder_id("quant-container", 1))
        self.assertNotEqual(holder, runtime_lease_holder_id("quant-container", 2))
        self.assertNotEqual(holder, runtime_lease_holder_id("other-container", 1))


def _disposable_database() -> bool:
    return bool(os.getenv("PGHOST")) and os.getenv("PGDATABASE", "").startswith("quant_test_")


@unittest.skipUnless(_disposable_database(), "requires the release-path disposable PostgreSQL database")
class RuntimeLeaseDatabaseTests(unittest.IsolatedAsyncioTestCase):
    def test_sync_acquire_renews_own_unexpired_lease_and_rejects_other_holder(self) -> None:
        database = Database()
        self.addCleanup(database.close)
        lease_key = f"test_runtime_lease_identity:{uuid.uuid4()}"
        holder = uuid.uuid4()
        other_holder = uuid.uuid4()

        self.assertTrue(acquire_runtime_lease(database, lease_key, holder, 120))
        with database.transaction() as connection:
            before = connection.execute(
                "SELECT holder_id,expires_at FROM quant.runtime_leases WHERE lease_key=%s", (lease_key,)
            ).fetchone()
        self.assertTrue(acquire_runtime_lease(database, lease_key, holder, 120))
        with database.transaction() as connection:
            renewed = connection.execute(
                "SELECT holder_id,expires_at FROM quant.runtime_leases WHERE lease_key=%s", (lease_key,)
            ).fetchone()
        self.assertEqual(renewed["holder_id"], holder)
        self.assertGreater(renewed["expires_at"], before["expires_at"])
        self.assertFalse(acquire_runtime_lease(database, lease_key, other_holder, 120))

        with database.transaction() as connection:
            connection.execute(
                "UPDATE quant.runtime_leases SET expires_at=now() - interval '1 second' WHERE lease_key=%s",
                (lease_key,),
            )
        self.assertTrue(acquire_runtime_lease(database, lease_key, other_holder, 120))

    async def test_async_acquire_renews_own_unexpired_lease_and_rejects_other_holder(self) -> None:
        database = AsyncDatabase()
        self.addAsyncCleanup(database.close)
        lease_key = f"test_runtime_lease_identity:{uuid.uuid4()}"
        holder = uuid.uuid4()
        other_holder = uuid.uuid4()

        self.assertTrue(await acquire_async(database, lease_key, holder, 120))
        async with database.transaction() as connection:
            result = await connection.execute(
                "SELECT holder_id,expires_at FROM quant.runtime_leases WHERE lease_key=%s", (lease_key,)
            )
            before = await result.fetchone()
        self.assertTrue(await acquire_async(database, lease_key, holder, 120))
        async with database.transaction() as connection:
            result = await connection.execute(
                "SELECT holder_id,expires_at FROM quant.runtime_leases WHERE lease_key=%s", (lease_key,)
            )
            renewed = await result.fetchone()
        self.assertEqual(renewed["holder_id"], holder)
        self.assertGreater(renewed["expires_at"], before["expires_at"])
        self.assertFalse(await acquire_async(database, lease_key, other_holder, 120))

        async with database.transaction() as connection:
            await connection.execute(
                "UPDATE quant.runtime_leases SET expires_at=now() - interval '1 second' WHERE lease_key=%s",
                (lease_key,),
            )
        self.assertTrue(await acquire_async(database, lease_key, other_holder, 120))


if __name__ == "__main__":
    unittest.main()
