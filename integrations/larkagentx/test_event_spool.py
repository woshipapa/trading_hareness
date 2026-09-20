import json
import tempfile
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from event_spool import EventSpool


class EventSpoolTests(unittest.TestCase):
    def test_enqueue_claim_and_delivery_are_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            spool = EventSpool(Path(directory) / "events.sqlite3")
            payload = {"msg_id": "m1", "content": "hello"}
            self.assertEqual(spool.enqueue("e1", payload), "queued")
            self.assertEqual(spool.enqueue("e1", {"msg_id": "different"}), "queued")
            self.assertEqual(spool.claim("e1"), "claimed")
            self.assertEqual(spool.claim("e1"), "in_flight")
            spool.mark_delivered("e1")
            self.assertEqual(spool.claim("e1"), "delivered")
            self.assertEqual(spool.stats(), {"queued": 0, "processing": 0, "delivered": 1, "failed": 0, "pending": 0, "drain_cursor": 1})
            self.assertEqual(spool.due(), [])
            spool.close()

    def test_failed_event_is_available_for_replay_and_payload_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.sqlite3"
            spool = EventSpool(path)
            payload = {"event_id": "e2", "nested": {"text": "中文"}}
            spool.enqueue("e2", payload)
            spool.claim("e2")
            spool.mark_failed("e2", "network", retry_after=1)
            # Make it immediately due without relying on wall-clock sleeps.
            spool._db.execute("UPDATE events SET available_at=0")
            spool._db.commit()
            due = spool.due()
            self.assertEqual(due[0]["event_id"], "e2")
            self.assertEqual(due[0]["payload"], payload)
            self.assertEqual(due[0]["last_error"], "network")
            spool.close()

    def test_drain_cursor_advances_only_over_a_contiguous_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            spool = EventSpool(Path(directory) / "events.sqlite3")
            spool.enqueue("e1", {"n": 1})
            spool.enqueue("e2", {"n": 2})
            spool.claim("e1")
            spool.claim("e2")
            spool.mark_delivered("e2")
            self.assertEqual(spool.stats()["drain_cursor"], 0)
            spool.mark_delivered("e1")
            self.assertEqual(spool.stats()["drain_cursor"], 2)
            spool.close()

    def test_payload_limit_rejects_oversized_event(self):
        with tempfile.TemporaryDirectory() as directory:
            spool = EventSpool(Path(directory) / "events.sqlite3", max_event_bytes=16 * 1024)
            with self.assertRaises(ValueError):
                spool.enqueue("large", {"text": "x" * 20_000})
            spool.close()


if __name__ == "__main__":
    unittest.main()
