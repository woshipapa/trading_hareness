import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from send_gate import SendRateLimited
from urgent_alerts import check_hourly_limit, pick_sent_message, recipient_groups, resolve_recipients, send_urgent


class FakeClient:
	def __init__(self, members=None, messages=None, gateway_error=None):
		self.members = members or {"account": 1, "person": 1}
		self.messages = messages or [{"msg_id": "sent", "from_id": "account", "position": 11}]
		self.gateway_error = gateway_error
		self.sent = []
		self.api_calls = []

	def api(self, name, payload):
		self.api_calls.append((name, payload))
		if name == "chats.PullChatChattersV2Request":
			return {"chatterJoinTimeSec": self.members}
		if name == "urgent.PutUrgentRequest":
			return {"urgent": {"messageId": payload["messageId"]}}
		raise AssertionError(name)

	def chat_infos(self, _chat_ids):
		return {"chat": {"lastMessagePosition": 11}}

	def pull_history(self, _chat_id, _positions):
		return self.messages

	def _gateway_post(self, packet):
		if self.gateway_error:
			raise RuntimeError(self.gateway_error)
		self.sent.append(packet)


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

	def test_sent_message_is_the_newest_message_from_the_account(self):
		messages = [{"msg_id": "old", "from_id": "account", "position": 10}, {"msg_id": "other", "from_id": "person", "position": 12}, {"msg_id": "new", "from_id": "account", "position": 13}]
		self.assertEqual(pick_sent_message(messages, "account"), "new")

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


if __name__ == "__main__":
	unittest.main()
