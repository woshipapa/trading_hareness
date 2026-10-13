import unittest

from chat_bots import ChatBotError, custom_bot_template, custom_bots, ensure_custom_bot, remove_custom_bot

ME = "7551451231416434716"
HOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/11111111-2222-3333-4444-555555555555"
CANDIDATES = {"functionalBots": {"6511584912533250048": {"id": "6511584912533250048", "name": "自定义机器人", "enName": "Custom Bot", "avatar": {"key": "587900163a9b2394c18c"}}}}


def listing(*bots, limit=15):
	"""A PullChatBots answer: (bot_id, name, creator_id, collection type)."""
	collections = {}
	for bot_id, name, creator, kind in bots:
		collections.setdefault(kind, {})[bot_id] = {"id": bot_id, "name": name, "creatorId": creator}
	return {"maxBotNum": limit, "botCollection": [{"type": kind, "bots": found} for kind, found in collections.items()]}


class FakeFeishu:
	"""The group's bots and the chatbot interface, enough to follow what the rules do to them."""

	def __init__(self, bots=(), limit=15, refuse=None):
		self.bots = {bot_id: {"name": name, "creator": creator, "kind": kind, "key_words": [], "signature_status": False, "allow_ips": [], "enable": True}
		             for bot_id, name, creator, kind in bots}
		self.limit, self.refuse, self.calls, self.next_id = limit, refuse or {}, [], 7695935537441721312

	def pull_chat_bots(self, chat_id):
		return listing(*[(bot_id, bot["name"], bot["creator"], bot["kind"]) for bot_id, bot in self.bots.items()], limit=self.limit)

	def pull_candidate_bots(self, chat_id):
		return CANDIDATES

	def call(self, name, body):
		self.calls.append((name, dict(body)))
		if name in self.refuse:
			return {"code": self.refuse[name][0], "msg": self.refuse[name][1]}
		if name == "CreateWebhookBot":
			bot_id = str(self.next_id)
			self.bots[bot_id] = {"name": body["name"], "creator": body["user_id"], "kind": 1, "key_words": [], "signature_status": False, "allow_ips": [], "enable": True}
			return {"code": 0, "msg": "", "data": {"bot_id": bot_id}}
		bot = self.bots[body["bot_id"]]
		if name == "GetWebhookBotInfo":
			return {"code": 0, "data": {"bot_id": body["bot_id"], "name": bot["name"], "avatar_key": "k", "avatar_type": 0, "description": "d", "enable": bot["enable"],
			                            "check_mender": False, "key_words": list(bot["key_words"]), "signature_status": bot["signature_status"], "allow_ips": list(bot["allow_ips"]),
			                            "no_permission": bot.get("no_permission", False), "webhook": bot.get("webhook", HOOK)}}
		if name == "UpdateWebhookBotInfo":
			bot.update(key_words=list(body["key_words"]), signature_status=body["signature_status"], allow_ips=list(body["allow_ips"]), enable=body["enable"])
			return {"code": 0, "data": {}}
		if name == "DeleteBotResource":
			del self.bots[body["bot_id"]]
			return {"code": 0, "data": {}}
		raise AssertionError(name)

	def ensure(self, **kwargs):
		return ensure_custom_bot(chat_id="7694513051067796759", user_id=ME, pull_chat_bots=self.pull_chat_bots, pull_candidate_bots=self.pull_candidate_bots,
		                         call=self.call, **{"keyword": "转发", **kwargs})


class EnsureCustomBotTests(unittest.TestCase):
	def test_a_group_without_the_bot_gets_one_with_the_keyword_as_its_only_security_setting(self):
		feishu = FakeFeishu(bots=[("1", "运维信息通知", ME, 1), ("2", "papa-bot", ME, 2)])
		result = feishu.ensure()
		self.assertEqual(result, {"chat_id": "7694513051067796759", "bot_id": "7695935537441721312", "name": "转发机器人", "keyword": "转发", "webhook": HOOK, "created": True})
		self.assertEqual([name for name, _ in feishu.calls], ["CreateWebhookBot", "GetWebhookBotInfo", "UpdateWebhookBotInfo", "GetWebhookBotInfo"])
		created = feishu.calls[0][1]
		self.assertEqual({key: created[key] for key in ("bot_type_id", "avatar_key", "avatar_type", "chat_id", "user_id")},
		                 {"bot_type_id": "6511584912533250048", "avatar_key": "587900163a9b2394c18c", "avatar_type": 0, "chat_id": "7694513051067796759", "user_id": ME})
		updated = feishu.calls[2][1]
		self.assertEqual((updated["key_words"], updated["signature_status"], updated["allow_ips"], updated["enable"]), (["转发"], False, [], True))

	def test_a_second_call_reuses_the_bot_and_changes_nothing(self):
		feishu = FakeFeishu()
		first = feishu.ensure()
		feishu.calls.clear()
		second = feishu.ensure()
		self.assertEqual(second, {**first, "created": False})
		self.assertEqual([name for name, _ in feishu.calls], ["GetWebhookBotInfo"])
		self.assertEqual(len(feishu.bots), 1)

	def test_another_keyword_is_added_and_signing_is_switched_off_on_the_relays_own_bot(self):
		feishu = FakeFeishu(bots=[("9", "转发机器人", ME, 1)])
		feishu.bots["9"].update(key_words=["旧词"], signature_status=True, allow_ips=["1.2.3.4"])
		result = feishu.ensure(keyword="新词")
		self.assertEqual((result["bot_id"], result["created"]), ("9", False))
		self.assertEqual((feishu.bots["9"]["key_words"], feishu.bots["9"]["signature_status"], feishu.bots["9"]["allow_ips"]), (["旧词", "新词"], False, []))

	def test_a_bot_of_the_same_name_made_by_someone_else_is_not_reused(self):
		feishu = FakeFeishu(bots=[("9", "转发机器人", "someone-else", 1)])
		result = feishu.ensure()
		self.assertTrue(result["created"])
		self.assertNotEqual(result["bot_id"], "9")
		self.assertEqual(feishu.bots["9"]["key_words"], [])

	def test_a_full_group_and_a_group_that_takes_no_custom_bot_are_refused_before_anything_is_created(self):
		full = FakeFeishu(bots=[(str(i), f"bot{i}", ME, 2) for i in range(15)])
		with self.assertRaisesRegex(ChatBotError, "已经到上限（15 个）"):
			full.ensure()
		self.assertEqual(full.calls, [])
		closed = FakeFeishu()
		closed.pull_candidate_bots = lambda chat_id: {"botCollection": []}
		with self.assertRaisesRegex(ChatBotError, "不能添加自定义机器人"):
			closed.ensure()
		self.assertEqual(closed.calls, [])

	def test_a_refusal_carries_the_servers_reason_and_never_a_webhook_address(self):
		refused = FakeFeishu(refuse={"CreateWebhookBot": (99991641, "session is invalid")})
		with self.assertRaisesRegex(ChatBotError, "创建机器人被拒绝：session is invalid"):
			refused.ensure()
		odd = FakeFeishu(bots=[("9", "转发机器人", ME, 1)])
		odd.bots["9"]["webhook"] = "https://example.com/not-a-hook"
		with self.assertRaises(ChatBotError) as raised:
			odd.ensure()
		self.assertNotIn("example.com", str(raised.exception))
		hidden = FakeFeishu(bots=[("9", "转发机器人", ME, 1)])
		hidden.bots["9"]["no_permission"] = True
		with self.assertRaisesRegex(ChatBotError, "没有权限"):
			hidden.ensure()

	def test_bad_input_is_refused_without_a_call(self):
		feishu = FakeFeishu()
		for kwargs in ({"keyword": " "}, {"keyword": "字" * 41}, {"name": ""}):
			with self.assertRaises(ValueError):
				feishu.ensure(**kwargs)
		with self.assertRaises(ValueError):
			ensure_custom_bot(chat_id="oc_abc", keyword="转发", user_id=ME, pull_chat_bots=feishu.pull_chat_bots, pull_candidate_bots=feishu.pull_candidate_bots, call=feishu.call)
		self.assertEqual(feishu.calls, [])


class RemoveCustomBotTests(unittest.TestCase):
	def remove(self, feishu, bot_id):
		return remove_custom_bot(chat_id="7694513051067796759", bot_id=bot_id, user_id=ME, pull_chat_bots=feishu.pull_chat_bots, call=feishu.call)

	def test_only_the_relays_own_bot_is_removed(self):
		feishu = FakeFeishu(bots=[("9", "转发机器人", ME, 1), ("8", "汇总bot", ME, 1), ("7", "转发机器人", "someone-else", 1)])
		self.assertEqual(self.remove(feishu, "9")["status"], "removed")
		self.assertNotIn("9", feishu.bots)
		for other in ("8", "7"):
			with self.assertRaisesRegex(ChatBotError, "不是转发看板创建的"):
				self.remove(feishu, other)
		self.assertEqual(sorted(feishu.bots), ["7", "8"])
		self.assertEqual(self.remove(feishu, "12345")["status"], "not_found")


class ListingTests(unittest.TestCase):
	def test_custom_bots_are_the_first_collection_only(self):
		answer = listing(("1", "汇总bot", ME, 1), ("2", "papa-bot", ME, 2))
		self.assertEqual(custom_bots(answer), [{"bot_id": "1", "name": "汇总bot", "creator_id": ME}])
		self.assertEqual(custom_bot_template(CANDIDATES), {"id": "6511584912533250048", "avatar_key": "587900163a9b2394c18c"})


if __name__ == "__main__":
	unittest.main()
