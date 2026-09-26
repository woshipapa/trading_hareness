import unittest
from datetime import datetime, timedelta, timezone

from app.owner_deploy_events import owner_deploy_status


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, *rows):
        self._rows = list(rows)

    def execute(self, _sql, _params=()):
        return _Result(self._rows.pop(0))


class OwnerDeployEventTests(unittest.TestCase):
    def test_missing_table_is_unavailable_but_not_a_write_pause(self):
        result = owner_deploy_status(_Connection({"present": False}))
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["pause_writes"])

    def test_idle_when_no_starting_event_has_been_left_open(self):
        result = owner_deploy_status(_Connection({"present": True}, None))
        self.assertEqual(result["status"], "idle")
        self.assertFalse(result["active"])

    def test_shared_tunnel_deploy_requests_write_pause(self):
        result = owner_deploy_status(_Connection(
            {"present": True},
            {
                "event_id": 7,
                "deploy_id": "deploy-1",
                "phase": "starting",
                "release_id": "release-1",
                "surfaces": {"http_api": True, "shared_tunnel": True},
                "expected_seconds": 10,
                "recorded_at": datetime.now(timezone.utc) - timedelta(seconds=30),
            },
        ))
        self.assertEqual(result["status"], "in_progress")
        self.assertTrue(result["active"])
        self.assertTrue(result["pause_writes"])
        self.assertEqual(result["deploy_id"], "deploy-1")

    def test_tuple_rows_are_supported(self):
        result = owner_deploy_status(_Connection(
            (True,),
            (1, "deploy-2", "starting", "release-2", {"shared_tunnel": False}, 12, None),
        ))
        self.assertEqual(result["status"], "in_progress")
        self.assertFalse(result["pause_writes"])

    def test_old_starting_event_is_reported_stale_without_pausing_writes(self):
        result = owner_deploy_status(_Connection(
            {"present": True},
            {
                "event_id": 7,
                "deploy_id": "deploy-old",
                "phase": "starting",
                "release_id": "release-old",
                "surfaces": {"http_api": True, "shared_tunnel": True},
                "expected_seconds": 10,
                "recorded_at": datetime(2026, 9, 20, 6, tzinfo=timezone.utc),
            },
        ))
        self.assertEqual(result["status"], "stale")
        self.assertFalse(result["active"])
        self.assertFalse(result["pause_writes"])


if __name__ == "__main__":
    unittest.main()
