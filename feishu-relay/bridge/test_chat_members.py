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
	def __init__(self, owner="100", pages=None):
		self.owner = owner
		self.pages = list(pages or [])
		self.calls = []

	def chat_infos(self, chat_ids):
		return {chat_ids[0]: {"ownerId": self.owner}}

	def get_user_name(self, user_id, _chat_id):
		return f"用户{user_id}"

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
			{"user_id": "100", "name": "用户100", "joined_at": 10},
			{"user_id": "200", "name": "用户200", "joined_at": 20},
			{"user_id": "300", "name": "用户300", "joined_at": 30},
		]})
		self.assertEqual(client.calls, [
			("chats.PullChatChattersV2Request", {"chatId": "7001", "limit": 100}),
			("chats.PullChatChattersV2Request", {"chatId": "7001", "limit": 100, "cursor": 9}),
		])

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

	def test_gateway_failure_surfaces_sid_and_account_cannot_be_removed(self):
		client = Client(pages=[{"sid": "拒绝原因", "payloadType": 4012}])
		with self.assertRaisesRegex(GroupAdminError, "拒绝原因"):
			ROUTES["/chat-members/list"](Bridge(client), {"chat_id": "7001"})
		client = Client()
		with self.assertRaisesRegex(GroupAdminError, "不能移除本账号"):
			ROUTES["/chat-members/remove"](Bridge(client), {"chat_id": "7001", "user_id": "100"})
		self.assertEqual(client.calls, [])


if __name__ == "__main__":
	unittest.main()
