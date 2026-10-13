import unittest

from target_groups import ROUTES, create, invite, post_permission
from group_admin import GroupAdminError


class FakeClient:
	def __init__(self, *, owner="owner"):
		self.owner = owner
		self.calls = []
		self.infos = {"7690000000000000001": {"ownerId": owner}}

	def api(self, name, payload):
		self.calls.append((name, payload))
		if name == "chats.PutChatRequest":
			return {"chat": {"id": "7690000000000000002", "name": payload["groupName"]}, "feedCard": {}}
		if name == "chats.PullChatLinkTokenRequest":
			return {"sharedUrl": "https://applink.feishu.cn/client/chat/chatter/add_by_link?link_token=token", "kouLing": "口令", "expireTime": 1}
		return {"payloadType": 1, "cmd": 1}

	def chat_infos(self, chat_ids):
		return {chat_id: self.infos[chat_id] for chat_id in chat_ids if chat_id in self.infos}


class FakeBridge:
	def __init__(self, client, user_id="owner"):
		self.client = client
		self.auth = type("Auth", (), {"user_id": user_id})()
		self._joined_groups_cache = (1, [])


class TargetGroupsTest(unittest.TestCase):
	def test_routes_are_exposed(self):
		self.assertEqual(set(ROUTES), {"/target-groups/create", "/target-groups/post-permission", "/target-groups/invite"})

	def test_create_sends_exact_documented_fields_and_drops_cache(self):
		client = FakeClient()
		bridge = FakeBridge(client)
		result = create(bridge, {"name": "新同步群", "description": "用于转发"})
		name, payload = client.calls[0]
		self.assertEqual(name, "chats.PutChatRequest")
		self.assertEqual(set(payload), {"type", "groupName", "groupDesc", "isCrossTenant", "cid"})
		self.assertEqual(payload["type"], "GROUP")
		self.assertEqual(payload["groupName"], "新同步群")
		self.assertEqual(payload["groupDesc"], "用于转发")
		self.assertTrue(payload["isCrossTenant"])
		self.assertRegex(payload["cid"], r"^[0-9a-f]{32}$")
		self.assertEqual(result, {"status": "ok", "chat_id": "7690000000000000002", "name": "新同步群"})
		self.assertIsNone(bridge._joined_groups_cache)

	def test_non_owner_is_refused_before_any_write_command(self):
		client = FakeClient(owner="other")
		bridge = FakeBridge(client)
		with self.assertRaises(GroupAdminError):
			post_permission(bridge, {"chat_id": "7690000000000000001", "only_owner": True})
		with self.assertRaises(GroupAdminError):
			invite(bridge, {"chat_id": "7690000000000000001"})
		self.assertEqual(client.calls, [])

	def test_post_permission_and_invite_use_documented_fields(self):
		client = FakeClient()
		bridge = FakeBridge(client)
		self.assertEqual(post_permission(bridge, {"chat_id": "7690000000000000001", "only_owner": True}), {"status": "ok", "chat_id": "7690000000000000001", "only_owner": True})
		self.assertEqual(invite(bridge, {"chat_id": "7690000000000000001"}), {"status": "ok", "chat_id": "7690000000000000001", "shared_url": "https://applink.feishu.cn/client/chat/chatter/add_by_link?link_token=token", "kou_ling": "口令", "expire_time": 1})
		self.assertEqual(client.calls[0], ("chats.PatchChatPostUserRequest", {"chatId": "7690000000000000001", "postType": "ONLY_ADMIN"}))
		self.assertEqual(client.calls[1], ("chats.PullChatLinkTokenRequest", {"chatId": "7690000000000000001", "expiredDay": 7}))


if __name__ == "__main__":
	unittest.main()
