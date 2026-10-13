import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chat_members import ROUTES
from group_admin import GroupAdminError


class Auth:
	user_id = "100"


class NameCache:
	def resolve(self, _chat_id, user_ids, _lookup):
		return {str(user_id): f"用户{user_id}" for user_id in user_ids}


class Client:
	def __init__(self, owner="100", pages=None, bots=None):
		self.owner = owner
		self.pages = list(pages or [])
		self.bots = bots or {}
		self.calls = []
		self.chat_bot_calls = []

	def chat_infos(self, chat_ids):
		return {chat_ids[0]: {"ownerId": self.owner}}

	def get_user_name(self, user_id, _chat_id):
		return f"用户{user_id}"

	def chat_bots(self, chat_id):
		self.chat_bot_calls.append(chat_id)
		return self.bots

	def api(self, command, payload):
		self.calls.append((command, payload))
		if self.pages:
			answer = self.pages.pop(0)
			return answer
		return {"payloadType": 1}


class Bridge:
	auth = Auth()

	def __init__(self, client):
		self.client = client
		self.name_cache = NameCache()


class ChatMembersTest(unittest.TestCase):
	def test_every_handler_checks_owner_before_gateway(self):
		for path, payload in [
			("/chat-members/list", {"chat_id": "7001"}),
			("/chat-members/history", {"chat_id": "7001"}),
			("/chat-members/applications", {"chat_id": "7001"}),
			("/chat-members/applications/decide", {"chat_id": "7001", "id": 1, "agree": True}),
			("/chat-members/remove", {"chat_id": "7001", "user_id": "200"}),
		]:
			client = Client(owner="999")
			with self.subTest(path=path), self.assertRaises(GroupAdminError):
				ROUTES[path](Bridge(client), payload)
			self.assertEqual(client.calls, [])

	def test_member_list_follows_cursor_and_includes_account(self):
		client = Client(pages=[
			{"chatterJoinTimeSec": {"100": 10, "200": 20}, "nextCursor": 9},
			{"chatterJoinTimeSec": {"300": 30}, "nextCursor": 0},
		])
		answer = ROUTES["/chat-members/list"](Bridge(client), {"chat_id": "7001"})
		self.assertEqual(answer, {"status": "ok", "chat_id": "7001", "members": [
			{"user_id": "100", "name": "用户100", "alias": "", "joined_at": 10, "is_bot": False, "is_self": True},
			{"user_id": "200", "name": "用户200", "alias": "", "joined_at": 20, "is_bot": False, "is_self": False},
			{"user_id": "300", "name": "用户300", "alias": "", "joined_at": 30, "is_bot": False, "is_self": False},
		]})
		self.assertEqual(client.calls, [
			("chats.PullChatChattersV2Request", {"chatId": "7001", "limit": 100}),
			("chats.PullChatChattersV2Request", {"chatId": "7001", "limit": 100, "cursor": 9}),
			("chatters.PullChattersByIdsRequest", {"chatterIds": ["200", "300"]}),
		])

	def test_member_list_carries_the_alias_the_account_gave_each_person(self):
		client = Client(pages=[
			{"chatterJoinTimeSec": {"100": 10, "200": 20, "300": 30}},
			{"chatters": {"200": {"alias": "阿明 108三个月"}, "300": {"name": "没有备注的人"}}},
		])
		answer = ROUTES["/chat-members/list"](Bridge(client), {"chat_id": "7001"})
		self.assertEqual([(member["user_id"], member["alias"]) for member in answer["members"]], [("100", ""), ("200", "阿明 108三个月"), ("300", "")])

	def test_member_list_marks_custom_and_app_bots_and_the_account(self):
		client = Client(
			pages=[{"chatterJoinTimeSec": {"100": 10, "bot-custom": 20, "bot-app": 30}}],
			bots={"botCollection": [
				{"type": 1, "bots": {"custom-key": {"id": "bot-custom", "name": "转发机器人"}}},
				{"type": 2, "bots": {"bot-app": {"name": "应用"}}},
			]},
		)
		answer = ROUTES["/chat-members/list"](Bridge(client), {"chat_id": "7001"})
		self.assertEqual(answer["members"], [
			{"user_id": "100", "name": "用户100", "alias": "", "joined_at": 10, "is_bot": False, "is_self": True},
			{"user_id": "bot-custom", "name": "用户bot-custom", "alias": "", "joined_at": 20, "is_bot": True, "is_self": False},
			{"user_id": "bot-app", "name": "用户bot-app", "alias": "", "joined_at": 30, "is_bot": True, "is_self": False},
		])
		self.assertEqual(client.chat_bot_calls, ["7001"])

	def test_history_and_applications_map_declared_fields(self):
		client = Client(pages=[
			{"chatterHistory": [{"chatterId": "200", "action": 1, "event": 18, "chatterType": 1, "createTime": 123}], "hasMore": True, "nextCursor": "7"},
			{"applications": [{"id": 5, "status": 1, "applyTime": 456, "extraMessage": "请加入", "contactSummary": {"name": "申请人"}}]},
		])
		bridge = Bridge(client)
		self.assertEqual(ROUTES["/chat-members/history"](bridge, {"chat_id": "7001"}), {
			"status": "ok", "chat_id": "7001", "rows": [{"user_id": "200", "name": "用户200", "action": "join", "event": 18, "chatter_type": 1, "at": 123}], "next_cursor": "7",
		})
		self.assertEqual(ROUTES["/chat-members/applications"](bridge, {"chat_id": "7001"}), {
			"status": "ok", "chat_id": "7001", "applications": [{"id": 5, "status": 1, "apply_time": 456, "message": "请加入", "contact_summary": {"name": "申请人"}}],
		})

	def test_absent_protobuf_defaults_are_returned_as_empty_or_unknown(self):
		client = Client(pages=[
			{},
			{"chatterHistory": [{"chatterId": "200"}]},
			{},
		])
		bridge = Bridge(client)
		self.assertEqual(ROUTES["/chat-members/list"](bridge, {"chat_id": "7001"}), {
			"status": "ok", "chat_id": "7001", "members": [],
		})
		self.assertEqual(ROUTES["/chat-members/history"](bridge, {"chat_id": "7001"}), {
			"status": "ok", "chat_id": "7001", "rows": [{
				"user_id": "200", "name": "用户200", "action": "unknown", "event": None, "chatter_type": None, "at": None,
			}], "next_cursor": None,
		})
		self.assertEqual(ROUTES["/chat-members/applications"](bridge, {"chat_id": "7001"}), {
			"status": "ok", "chat_id": "7001", "applications": [],
		})

	def test_gateway_failure_surfaces_sid_and_account_cannot_be_removed(self):
		client = Client(pages=[{"sid": "拒绝原因", "payloadType": 4012}])
		with self.assertRaisesRegex(GroupAdminError, "拒绝原因"):
			ROUTES["/chat-members/list"](Bridge(client), {"chat_id": "7001"})
		client = Client()
		with self.assertRaisesRegex(GroupAdminError, "不能移除本账号"):
			ROUTES["/chat-members/remove"](Bridge(client), {"chat_id": "7001", "user_id": "100"})
		self.assertEqual(client.calls, [])

	def test_remove_refuses_a_bot(self):
		client = Client(bots={"botCollection": [{"type": 1, "bots": {"bot": {"id": "200"}}}]})
		with self.assertRaisesRegex(GroupAdminError, "机器人"):
			ROUTES["/chat-members/remove"](Bridge(client), {"chat_id": "7001", "user_id": "200"})
		self.assertEqual(client.calls, [])


if __name__ == "__main__":
	unittest.main()
