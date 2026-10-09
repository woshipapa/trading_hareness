import asyncio
import unittest
from datetime import datetime, timezone

from app.level1_snapshot_runtime import capture_level1_snapshot


class Level1SnapshotRuntimeTests(unittest.TestCase):
    def test_capture_persists_full_cross_section_as_raw_evidence(self):
        calls = []

        async def fetch():
            return ([{"symbol": "000001.SZ", "price": 10}], {"cross_sectional": True, "upstream_timestamp_ms": 1})

        async def persist(provider, capability, rows):
            calls.append((provider, capability, rows))
            return len(rows)

        async def open_session(_now):
            return True

        result = asyncio.run(capture_level1_snapshot(
            fetch_snapshot=fetch, persist=persist, session_open=open_session,
            now=datetime(2026, 8, 31, 1, 0, tzinfo=timezone.utc),
        ))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["stored"], 1)
        self.assertEqual(calls[0][0:2], ("fuyao_ths", "a_share_prices_snapshot"))
        self.assertTrue(calls[0][2][0]["research_only"])

    def test_a_derived_view_sees_the_stored_rows_and_its_failure_never_fails_the_capture(self):
        seen = []

        async def fetch():
            return ([{"symbol": "000001.SZ", "price": 10, "pct_change": 1.0, "turnover": 5.0}], {"upstream_timestamp_ms": 1})

        async def persist(_provider, _capability, rows):
            return len(rows)

        async def open_session(_now):
            return True

        async def derive(observed_at, rows):
            seen.append((observed_at, [row["ts_code"] for row in rows]))
            return {"status": "stored"}

        async def broken(_observed_at, _rows):
            raise RuntimeError("radar store unavailable")

        moment = datetime(2026, 10, 9, 2, 0, tzinfo=timezone.utc)
        result = asyncio.run(capture_level1_snapshot(
            fetch_snapshot=fetch, persist=persist, session_open=open_session, now=moment, on_persisted=derive))
        self.assertEqual(result["derived"], {"status": "stored"})
        self.assertEqual(seen, [(moment, ["000001.SZ"])])
        failed = asyncio.run(capture_level1_snapshot(
            fetch_snapshot=fetch, persist=persist, session_open=open_session, now=moment, on_persisted=broken))
        self.assertEqual(failed["status"], "completed")
        self.assertEqual(failed["derived"]["status"], "failed")

    def test_capture_fails_closed_outside_session(self):
        async def open_session(_now):
            return False

        async def fail_fetch():
            raise AssertionError("must not call provider outside session")

        async def persist(*_args):
            raise AssertionError("must not write outside session")

        result = asyncio.run(capture_level1_snapshot(
            fetch_snapshot=fail_fetch, persist=persist, session_open=open_session,
            now=datetime(2026, 8, 30, 1, 0, tzinfo=timezone.utc),
        ))
        self.assertEqual(result["status"], "outside_session")

    def test_capture_fails_closed_when_session_adapter_returns_reason_tuple(self):
        async def open_session(_now):
            return False, "outside SSE continuous auction sessions"

        async def fail_fetch():
            raise AssertionError("must not call provider outside session")

        async def persist(*_args):
            raise AssertionError("must not write outside session")

        result = asyncio.run(capture_level1_snapshot(
            fetch_snapshot=fail_fetch, persist=persist, session_open=open_session,
            now=datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc),
        ))
        self.assertEqual(result["status"], "outside_session")

    def test_capture_reports_provider_health_after_persisting_snapshot(self):
        health = []

        async def fetch():
            return ([{"symbol": "000001.SZ", "price": 10}], {"cross_sectional": True, "status": "unknown"})

        async def persist(*_args):
            return 1

        async def persist_health(result):
            health.append(result)

        async def open_session(_now):
            return True

        result = asyncio.run(capture_level1_snapshot(
            fetch_snapshot=fetch, persist=persist, persist_health=persist_health,
            session_open=open_session, now=datetime(2026, 8, 31, 1, 0, tzinfo=timezone.utc),
        ))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(health[0]["freshness_status"], "unknown")


if __name__ == "__main__":
    unittest.main()
