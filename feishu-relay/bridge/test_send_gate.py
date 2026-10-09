import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from send_gate import SendConflict, SendGate, SendRateLimited


class SendGateTests(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.path = Path(self.tmp.name) / "send.db"
		self.gate = SendGate(self.path, per_chat_per_min=2, global_per_hour=3)

	def tearDown(self):
		self.gate.close()
		self.tmp.cleanup()

	def admit(self, chat="oc_a", text="hi", key=None, root=None):
		return self.gate.begin(chat_id=chat, text=text, root_id=root, idempotency_key=key)

	def test_per_chat_limit_returns_retry_after(self):
		for i in range(2):
			self.gate.finish(self.admit(text=f"m{i}")["audit_id"], ok=True)
		with self.assertRaises(SendRateLimited) as ctx:
			self.admit(text="m3")
		self.assertEqual(ctx.exception.scope, "chat")
		self.assertTrue(1 <= ctx.exception.retry_after <= 61)

	def test_other_chat_unaffected_until_global_limit(self):
		for i in range(2):
			self.gate.finish(self.admit(text=f"m{i}")["audit_id"], ok=True)
		self.gate.finish(self.admit(chat="oc_b")["audit_id"], ok=True)
		with self.assertRaises(SendRateLimited) as ctx:
			self.admit(chat="oc_c")
		self.assertEqual(ctx.exception.scope, "global")

	def test_failed_send_does_not_consume_budget(self):
		for i in range(5):
			self.gate.finish(self.admit(text=f"m{i}")["audit_id"], ok=False, error="boom")
		self.assertIn("audit_id", self.admit(text="ok"))

	def test_idempotent_replay_returns_first_result_without_budget(self):
		first = self.admit(key="k1")
		self.gate.finish(first["audit_id"], ok=True)
		again = self.admit(key="k1")
		self.assertEqual(again["replay"]["id"], first["audit_id"])
		self.assertEqual(again["replay"]["result"], "accepted")
		self.assertEqual(len(self.gate.audit()), 1)

	def test_same_key_different_content_conflicts(self):
		self.gate.finish(self.admit(key="k1")["audit_id"], ok=True)
		with self.assertRaises(SendConflict):
			self.admit(key="k1", text="other")

	def test_in_flight_key_conflicts_and_failed_key_may_retry(self):
		first = self.admit(key="k1")
		with self.assertRaises(SendConflict):
			self.admit(key="k1")
		self.gate.finish(first["audit_id"], ok=False, error="boom")
		self.assertIn("audit_id", self.admit(key="k1"))

	def test_audit_never_stores_text_and_restart_marks_pending_unknown(self):
		self.admit(text="secret body", key="k9")
		row = self.gate.audit()[0]
		self.assertNotIn("text", row)
		self.assertEqual(row["text_length"], len("secret body"))
		self.gate.close()
		self.gate = SendGate(self.path, per_chat_per_min=2, global_per_hour=3)
		self.assertEqual(self.gate.audit()[0]["result"], "unknown")
		self.assertIn("replay", self.admit(text="secret body", key="k9"))

	def test_stats_reports_remaining(self):
		self.gate.finish(self.admit()["audit_id"], ok=True)
		stats = self.gate.stats(["oc_a", "oc_b"])
		self.assertEqual(stats["chat_remaining"], {"oc_a": 1, "oc_b": 2})
		self.assertEqual(stats["global_remaining"], 2)


if __name__ == "__main__":
	unittest.main()
