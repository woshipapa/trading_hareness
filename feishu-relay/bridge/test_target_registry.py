import hashlib
import unittest

from target_registry import resolve


HOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/test-hook"


class FakeAuth:
	user_id = "owner"


class FakeClient:
	def __init__(self):
		self.webhook_calls = []

	def feed_page(self, feed, cursor):
		return {"cards": [], "chats": {"7001": {"id": "7001", "type": 2, "name": "目标群"}}}

	def chat_infos(self, chat_ids):
		return {"7001": {"id": "7001", "ownerId": "owner"}}

	def chat_bots(self, chat_id):
		return {"botCollection": [{"type": 1, "bots": {"bot": {"id": "bot", "name": "转发机器人", "creatorId": "owner"}}}]}

	def chatbot_api(self, name, body):
		self.webhook_calls.append((name, body))
		return {"code": 0, "data": {"name": "转发机器人", "key_words": ["自测"], "webhook": HOOK}}


class FakeBridge:
	auth = FakeAuth()

	def __init__(self):
		self.client = FakeClient()


class TargetRegistryTest(unittest.TestCase):
	def test_only_a_requested_hash_is_returned_and_the_address_is_not(self):
		bridge = FakeBridge()
		answer = resolve(bridge, {"webhook_hashes": [hashlib.sha256(HOOK.encode()).hexdigest()]})
		bot = answer["groups"][0]["bots"][0]
		self.assertEqual(bot["webhook_hash"], hashlib.sha256(HOOK.encode()).hexdigest())
		self.assertNotIn("webhook", bot)
		self.assertNotIn(HOOK, repr(answer))

	def test_unrequested_or_refused_webhooks_have_no_hash(self):
		bridge = FakeBridge()
		bridge.client.chatbot_api = lambda name, body: {"code": 403, "msg": "refused", "data": {"webhook": HOOK}}
		answer = resolve(bridge, {"webhook_hashes": []})
		self.assertNotIn("webhook_hash", answer["groups"][0]["bots"][0])


if __name__ == "__main__":
	unittest.main()
