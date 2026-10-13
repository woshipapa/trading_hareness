import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

from send_gate import SendRateLimited
from urgent_alerts import check_hourly_limit, pick_sent_message, recipient_groups, reserve_urgent_slot, resolve_recipients, send_urgent


class FakeClient:
	def __init__(self, members=None, messages=None, gateway_error=None, gateway_response=None, positions=(10, 11)):
		self.members = members or {"account": 1, "person": 1}
		self.messages = messages or [{"msg_id": "sent", "from_id": "account", "content": "告警", "position": 11}]
		self.gateway_error = gateway_error
		self.gateway_response = gateway_response or {"payloadType": 1}
		self.positions = iter(positions)
		self.sent = []
		self.api_calls = []
		self.history_calls = []

	def api(self, name, payload):
		self.api_calls.append((name, payload))
		if name == "chats.PullChatChattersV2Request":
			return {"chatterJoinTimeSec": self.members}
		if name == "urgent.PutUrgentRequest":
			return {"urgent": {"messageId": payload["messageId"]}}
		raise AssertionError(name)

	def chat_infos(self, _chat_ids):
		return {"chat": {"lastMessagePosition": next(self.positions)}}

	def pull_history(self, _chat_id, _positions):
		self.history_calls.append(list(_positions))
		return self.messages

	def _gateway_post(self, packet):
		if self.gateway_error:
			raise RuntimeError(self.gateway_error)
		self.sent.append(packet)
		return self.gateway_response


class FakeGate:
	def __init__(self):
		self.calls = []

	def begin(self, **kwargs):
		self.calls.append(("begin", kwargs))
		return {"audit_id": 9}

	def finish(self, audit_id, **kwargs):
		self.calls.append(("finish", audit_id, kwargs))


def bridge(client):
	return SimpleNamespace(auth=SimpleNamespace(user_id="account"), client=client, send_gate=FakeGate())


class UrgentAlertsTest(unittest.TestCase):
	def test_recipient_groups_only_include_two_person_chats_and_other_member(self):
		groups = [{"chat_id": "chat", "name": "单聊群"}, {"chat_id": "large", "name": "大群"}]
		infos = {"chat": {"userCount": 2, "name": "单聊群"}, "large": {"userCount": 3, "name": "大群"}}
		members = {"chat": ["account", "person"], "large": ["account", "person", "third"]}
		self.assertEqual(recipient_groups(groups, chat_infos=infos, member_ids=members.__getitem__, account_id="account"), [{"chat_id": "chat", "name": "单聊群", "user_id": "person"}])

	def test_recipient_route_reads_joined_groups_and_member_ids(self):
		client = FakeClient()
		client.feed_page = lambda feed, cursor: {"cards": [], "chats": {"7001": {"id": "7001", "type": 2, "name": "单聊群"}}}
		client.chat_infos = lambda _chat_ids: {"7001": {"userCount": 2, "name": "单聊群"}}
		self.assertEqual(resolve_recipients(bridge(client), {}), {"status": "ok", "recipients": [{"chat_id": "7001", "name": "单聊群", "user_id": "person"}]})

	def test_sent_message_matches_exact_text_across_interleaved_positions(self):
		messages = [{"msg_id": "other", "from_id": "person", "content": "别人的消息", "position": 11},
			{"msg_id": "sent", "from_id": "account", "content": "告警", "position": 13}]
		self.assertEqual(pick_sent_message(messages, "account", "告警"), "sent")

	def test_sent_message_requires_exactly_one_text_match(self):
		messages = [{"msg_id": "one", "from_id": "account", "content": "告警", "position": 11},
			{"msg_id": "two", "from_id": "account", "content": "告警", "position": 13}]
		with self.assertRaisesRegex(RuntimeError, "唯一"):
			pick_sent_message(messages, "account", "告警")

	def test_third_member_is_refused_before_any_send(self):
		client = FakeClient(members={"account": 1, "person": 1, "third": 1})
		fake = bridge(client)
		with self.assertRaisesRegex(Exception, "恰好只有"):
			send_urgent(fake, {"chat_id": "chat", "user_id": "person", "text": "告警"}, build_packet=Mock(), request_id=Mock())
		self.assertEqual(client.sent, [])
		self.assertEqual(fake.send_gate.calls, [])

	def test_wrong_user_is_refused_before_any_send(self):
		client = FakeClient()
		fake = bridge(client)
		with self.assertRaisesRegex(Exception, "恰好只有"):
			send_urgent(fake, {"chat_id": "chat", "user_id": "other", "text": "告警"}, build_packet=Mock(), request_id=Mock())
		self.assertEqual(client.sent, [])
		self.assertEqual(fake.send_gate.calls, [])

	def test_seventh_alert_is_limited_and_first_alert_after_window_is_admitted(self):
		with self.assertRaises(SendRateLimited):
			check_hourly_limit([0, 100, 200, 300, 400, 500], 500)
		self.assertEqual(check_hourly_limit([0, 100, 200, 300, 400, 500], 3601), [100, 200, 300, 400, 500, 3601])

	def test_send_is_finished_through_gate_and_failed_gateway_is_recorded(self):
		client = FakeClient()
		fake = bridge(client)
		result = send_urgent(fake, {"chat_id": "chat", "user_id": "person", "text": "告警"}, build_packet=lambda text, chat, request: (text, chat, request), request_id=lambda: "request")
		self.assertEqual(result, {"status": "ok", "message_id": "sent", "urgent": True})
		self.assertEqual([call[0] for call in fake.send_gate.calls], ["begin", "finish"])
		self.assertTrue(fake.send_gate.calls[-1][2]["ok"])

		failed_client = FakeClient(gateway_error="拒绝")
		failed = bridge(failed_client)
		with self.assertRaisesRegex(RuntimeError, "拒绝"):
			send_urgent(failed, {"chat_id": "chat", "user_id": "person", "text": "告警"}, build_packet=lambda *_: object(), request_id=lambda: "request")
		self.assertFalse(failed.send_gate.calls[-1][2]["ok"])

	def test_gateway_failure_envelope_finishes_failed_without_history_or_urgent(self):
		client = FakeClient(gateway_response={"payloadType": 4012, "sid": "拒绝发送"})
		fake = bridge(client)
		with self.assertRaisesRegex(Exception, "拒绝发送"):
			send_urgent(fake, {"chat_id": "chat", "user_id": "person", "text": "告警"}, build_packet=lambda *_: object(), request_id=lambda: "request")
		self.assertEqual(client.history_calls, [])
		self.assertEqual([name for name, _payload in client.api_calls if name == "urgent.PutUrgentRequest"], [])
		self.assertFalse(any(call[0] == "finish" and call[2]["ok"] for call in fake.send_gate.calls))

	def test_send_searches_positions_after_pre_send_position_for_exact_text(self):
		client = FakeClient(messages=[
			{"msg_id": "interleaving", "from_id": "account", "content": "另一条", "position": 11},
			{"msg_id": "sent", "from_id": "account", "content": "告警", "position": 13},
		], positions=(10, 13))
		fake = bridge(client)
		result = send_urgent(fake, {"chat_id": "chat", "user_id": "person", "text": "告警"}, build_packet=lambda *_: object(), request_id=lambda: "request")
		self.assertEqual(result["message_id"], "sent")
		self.assertEqual(client.api_calls[-1][1]["messageId"], "sent")

	def test_missing_exact_message_finishes_failed_without_marking_urgent(self):
		client = FakeClient(messages=[{"msg_id": "other", "from_id": "account", "content": "别的告警", "position": 13}], positions=(10, 13))
		fake = bridge(client)
		with self.assertRaisesRegex(RuntimeError, "唯一"):
			send_urgent(fake, {"chat_id": "chat", "user_id": "person", "text": "告警"}, build_packet=lambda *_: object(), request_id=lambda: "request")
		self.assertEqual([name for name, _payload in client.api_calls if name == "urgent.PutUrgentRequest"], [])
		self.assertFalse(fake.send_gate.calls[-1][2]["ok"])

	def test_concurrent_reservations_admit_only_six(self):
		fake = SimpleNamespace(_urgent_alert_timestamps=[])
		with ThreadPoolExecutor(max_workers=7) as pool:
			results = list(pool.map(lambda _item: self._reserve(fake), range(7)))
		self.assertEqual(sum(results), 6)

	@staticmethod
	def _reserve(bridge):
		try:
			reserve_urgent_slot(bridge, now=1_000)
			return 1
		except SendRateLimited:
			return 0


if __name__ == "__main__":
	unittest.main()
