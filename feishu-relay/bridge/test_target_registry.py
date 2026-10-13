import hashlib
import unittest

from group_admin import GroupAdminError
from target_registry import adopt_bot, list_bots, resolve


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
		return {"code": 0, "msg": "", "data": {"name": "转发机器人", "key_words": ["自测"], "webhook": HOOK}}


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


class ExistingBotRouteTest(unittest.TestCase):
	def test_an_owned_groups_bots_are_listed_without_addresses_and_one_can_be_handed_over(self):
		bridge = FakeBridge()
		listed = list_bots(bridge, {"chat_id": "7001"})
		self.assertEqual([(bot["bot_id"], bot["usable"]) for bot in listed["bots"]], [("bot", True)])
		self.assertNotIn(HOOK, repr(listed))
		bridge.client.chat_bots = lambda chat_id: {"botCollection": [{"type": 1, "bots": {"41": {"id": "41", "name": "dragon", "creatorId": "owner"}}}]}
		self.assertEqual(adopt_bot(bridge, {"chat_id": "7001", "bot_id": "41"})["webhook"], HOOK)

	def test_a_group_the_account_does_not_own_is_refused_before_any_bot_is_read(self):
		bridge = FakeBridge()
		bridge.client.chat_infos = lambda chat_ids: {"7001": {"id": "7001", "ownerId": "someone-else"}}
		for route in (list_bots, adopt_bot):
			with self.assertRaises(GroupAdminError):
				route(bridge, {"chat_id": "7001", "bot_id": "41"})
		self.assertEqual(bridge.client.webhook_calls, [])


if __name__ == "__main__":
	unittest.main()
