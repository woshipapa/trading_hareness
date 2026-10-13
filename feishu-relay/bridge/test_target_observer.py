import threading
import time
import unittest
from unittest.mock import patch

from group_admin import GroupAdminError
from target_observer import observe, recall_in_group, tap


class FakeAuth:
	user_id = "owner"


class FakeClient:
	def __init__(self, owner="owner"):
		self.owner = owner
		self.calls = []

	def chat_infos(self, chat_ids):
		return {chat_ids[0]: {"ownerId": self.owner}}

	def api(self, name, payload):
		self.calls.append((name, payload))
		return {"payloadType": 1}


class FakeBridge:
	auth = FakeAuth()
	ingress_url = "http://adapter:3000/internal/larkagentx/inbound"
	token = "test-token"

	def __init__(self, owner="owner"):
		self.client = FakeClient(owner)


class TargetObserverTest(unittest.TestCase):
	def test_tap_only_forwards_the_registered_bot_and_returns_before_post_finishes(self):
		bridge = FakeBridge()
		observe(bridge, {"chats": [{"chat_id": "7001", "bot_ids": ["bot-1"]}]})
		started = threading.Event()
		release = threading.Event()
		payloads = []

		def blocked_post(_bridge, payload):
			payloads.append(payload)
			started.set()
			release.wait(1)

		message = {"msg_id": "om_1", "position": 7, "create_time": 10, "from_id": "bot-1", "msg_type_name": "TEXT", "content": "自测 正文"}
		with patch("target_observer._post_observed", blocked_post):
			before = time.monotonic()
			tap(bridge, "7001", message)
			self.assertLess(time.monotonic() - before, 0.1)
			self.assertTrue(started.wait(1))
			tap(bridge, "7002", message)
			tap(bridge, "7001", {**message, "from_id": "somebody-else"})
			time.sleep(0.02)
			release.set()

		self.assertEqual(payloads, [{
			"chat_id": "7001", "message_id": "om_1", "position": 7, "create_time": 10,
			"from_id": "bot-1", "msg_type": "TEXT", "text": "自测 正文",
		}])

	def test_observe_replaces_the_whole_list(self):
		bridge = FakeBridge()
		self.assertEqual(observe(bridge, {"chats": [{"chat_id": "7001", "bot_ids": ["bot-1"]}]}), {"status": "ok", "observed": 1})
		self.assertEqual(observe(bridge, {"chats": []}), {"status": "ok", "observed": 0})
		self.assertEqual(bridge._target_observer_chats, {})

	def test_tap_drops_when_the_bounded_observation_queue_is_full(self):
		bridge = FakeBridge()
		observe(bridge, {"chats": [{"chat_id": "7001", "bot_ids": ["bot-1"]}]})
		started = threading.Event()
		release = threading.Event()

		def blocked_post(_bridge, _payload):
			started.set()
			release.wait(1)

		message = {"msg_id": "om_1", "from_id": "bot-1", "content": "正文"}
		with patch("target_observer._post_observed", blocked_post), patch("target_observer.OBSERVATION_QUEUE_SIZE", 1):
			tap(bridge, "7001", message)
			self.assertTrue(started.wait(1))
			tap(bridge, "7001", {**message, "msg_id": "om_2"})
			tap(bridge, "7001", {**message, "msg_id": "om_3"})
			self.assertEqual(bridge._target_observer_dropped, 1)
			release.set()
			bridge._target_observer_queue.join()

	def test_non_owner_cannot_recall(self):
		bridge = FakeBridge(owner="another-owner")
		with self.assertRaises(GroupAdminError):
			recall_in_group(bridge, {"chat_id": "7001", "message_id": "om_1"})
		self.assertEqual(bridge.client.calls, [])

	def test_owner_recall_uses_the_observed_gateway_command(self):
		bridge = FakeBridge()
		self.assertEqual(recall_in_group(bridge, {"chat_id": "7001", "message_id": "om_1"}), {"status": "ok", "chat_id": "7001", "message_id": "om_1"})
		self.assertEqual(bridge.client.calls, [("messages.DeleteGroupMessageRequest", {"id": "om_1"})])


if __name__ == "__main__":
	unittest.main()
