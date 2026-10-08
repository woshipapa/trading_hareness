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

    def test_failed_event_can_be_terminated_as_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            spool = EventSpool(Path(directory) / "events.sqlite3")
            payload = {
                "chat_id": "xhs-command-chat",
                "msg_id": "webhook-summary-1",
                "msg_type_name": "TEXT",
                "position": 101,
            }
            spool.enqueue("echo-1", payload)
            spool.claim("echo-1")
            spool.mark_failed("echo-1", "not configured", retry_after=1)

            self.assertTrue(spool.mark_ignored("echo-1", reason="xhs_command_chat_non_command"))
            self.assertFalse(spool.mark_ignored("echo-1", reason="xhs_command_chat_non_command"))
            self.assertEqual(spool.stats(), {
                "queued": 0,
                "processing": 0,
                "delivered": 1,
                "failed": 0,
                "pending": 0,
                "drain_cursor": 1,
            })
            self.assertEqual(spool.chat_stats()["xhs-command-chat"]["forwarded_count"], 0)
            self.assertEqual(spool.chat_stats()["xhs-command-chat"]["failed_count"], 0)
            self.assertEqual(spool.chat_stats()["xhs-command-chat"]["historical_failed_count"], 1)
            self.assertEqual(
                spool.ignored_stats()["xhs-command-chat"]["last_reason"],
                "xhs_command_chat_non_command",
            )
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

    def test_chat_metrics_survive_reopen_and_track_delivery_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.sqlite3"
            payload = {"chat_id": "7667390477875858612", "msg_id": "m1", "msg_type_name": "IMAGE"}
            spool = EventSpool(path)
            spool.enqueue("e1", payload)
            spool.claim("e1")
            spool.mark_delivered("e1")
            spool.enqueue("e2", {**payload, "msg_id": "m2"})
            spool.claim("e2")
            spool.mark_failed("e2", "temporary")
            self.assertEqual(spool.chat_summary(), {"observed_count": 2, "forwarded_count": 1, "failed_count": 1, "historical_failed_count": 1, "filtered_count": 0})
            self.assertEqual(spool.chat_stats()["7667390477875858612"]["last_message_type"], "IMAGE")
            spool.close()

            reopened = EventSpool(path)
            stats = reopened.chat_stats()["7667390477875858612"]
            self.assertEqual(stats["observed_count"], 2)
            self.assertEqual(stats["forwarded_count"], 1)
            self.assertEqual(stats["failed_count"], 1)
            self.assertEqual(stats["filtered_count"], 0)
            reopened.increment_counter("decode_error_count")
            reopened.close()

            final = EventSpool(path)
            self.assertEqual(final.counters()["decode_error_count"], 1)
            final.close()

    def test_position_gaps_and_ignored_chat_metrics_are_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.sqlite3"
            spool = EventSpool(path)
            self.assertIsNone(spool.record_position({"chat_id": "chat-a", "position": 10}))
            gap = spool.record_position({"chat_id": "chat-a", "position": 12})
            self.assertEqual(gap, {"previous": 10, "current": 12, "start": 11, "end": 11, "missing": 1})
            ignored_gap = spool.record_ignored("chat-unknown", position=3, message_id="m-3", message_type="IMAGE")
            self.assertIsNone(ignored_gap)
            ignored_gap = spool.record_ignored("chat-unknown", position=5, message_id="m-5", message_type="IMAGE")
            self.assertEqual(ignored_gap["missing"], 1)
            self.assertEqual(spool.ignored_stats()["chat-unknown"]["ignored_count"], 2)
            self.assertEqual(spool.ignored_summary(), {"ignored_count": 2})
            self.assertEqual(spool.position_stats()["chat-a"]["missing_position_count"], 1)
            self.assertEqual(spool.position_stats()["chat-unknown"]["last_gap_start"], 4)
            spool.close()

            reopened = EventSpool(path)
            self.assertEqual(reopened.ignored_stats()["chat-unknown"]["ignored_count"], 2)
            self.assertEqual(reopened.position_summary()["missing_position_count"], 2)
            reopened.close()

    def test_position_metrics_are_backfilled_from_existing_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.sqlite3"
            spool = EventSpool(path)
            spool.enqueue("e1", {"chat_id": "chat-a", "position": 20, "msg_id": "m1"})
            spool.enqueue("e2", {"chat_id": "chat-a", "position": 22, "msg_id": "m2"})
            spool.close()

            reopened = EventSpool(path)
            stats = reopened.position_stats()["chat-a"]
            self.assertEqual(stats["last_position"], 22)
            self.assertEqual(stats["missing_position_count"], 1)
            self.assertEqual(stats["last_gap_start"], 21)
            reopened.close()

    def test_private_recovery_is_durable_and_closes_unresolved_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.sqlite3"
            spool = EventSpool(path)
            spool.enqueue("e10", {"chat_id": "chat-a", "position": 10, "msg_id": "m10"})
            spool.enqueue("e12", {"chat_id": "chat-a", "position": 12, "msg_id": "m12"})
            self.assertEqual(spool.position_gap_ranges("chat-a"), [{"start": 11, "end": 11, "missing": 1}])
            self.assertTrue(spool.record_recovered_position("chat-a", 11))
            self.assertFalse(spool.record_recovered_position("chat-a", 11))
            stats = spool.position_stats()["chat-a"]
            self.assertEqual(stats["recovered_position_count"], 1)
            self.assertEqual(stats["unresolved_position_count"], 0)
            self.assertEqual(spool.position_gap_ranges("chat-a"), [])
            spool.close()

            reopened = EventSpool(path)
            self.assertEqual(reopened.position_summary()["recovered_position_count"], 1)
            self.assertEqual(reopened.position_summary()["unresolved_position_count"], 0)
            reopened.close()

    def test_filtered_event_is_durable_and_counts_as_observed_without_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.sqlite3"
            spool = EventSpool(path)
            self.assertTrue(spool.record_filtered(
                "larkagentx:chat-a:m11", "chat-a", message_id="m11", position=11,
                message_type="CARD", source_key="anqiang", keyword="般若星登山的川柏",
            ))
            self.assertFalse(spool.record_filtered(
                "larkagentx:chat-a:m11", "chat-a", message_id="m11", position=11,
                message_type="CARD", source_key="anqiang", keyword="般若星登山的川柏",
            ))
            self.assertEqual(spool.chat_stats()["chat-a"]["observed_count"], 1)
            self.assertEqual(spool.chat_stats()["chat-a"]["filtered_count"], 1)
            self.assertEqual(spool.chat_summary()["filtered_count"], 1)
            self.assertEqual(spool.position_gap_ranges("chat-a"), [])
            spool.close()


if __name__ == "__main__":
    unittest.main()
