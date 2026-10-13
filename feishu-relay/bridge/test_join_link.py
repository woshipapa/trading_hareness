import unittest

from group_admin import GroupAdminError
from join_link import join_by_link, link_info


TOKEN = "a" * 36
LINK = f"https://applink.feishu.cn/client/chat/chatter/add_by_link?link_token={TOKEN}"


class FakeClient:
	def __init__(self, answer):
		self.answer = answer
		self.calls = []

	def api(self, request, payload):
		self.calls.append((request, payload))
		return self.answer


class FakeBridge:
	auth = type("Auth", (), {"user_id": "owner"})()

	def __init__(self, answer):
		self.client = FakeClient(answer)
		self._joined_groups_cache = (1, [{"chat_id": "old"}])


class JoinLinkTest(unittest.TestCase):
	def test_invalid_links_are_rejected_before_gateway_call(self):
		for link in [
			"https://example.com/client/chat/chatter/add_by_link?link_token=" + TOKEN,
			"https://applink.feishu.cn/client/chat/chatter/add_by_link?link_token=short",
		]:
			bridge = FakeBridge({})
			with self.assertRaisesRegex(ValueError, "邀请链接"):
				link_info(bridge, {"link": link})
			self.assertEqual(bridge.client.calls, [])

	def test_link_info_maps_gateway_fields(self):
		bridge = FakeBridge({
			"alreadyInChat": True,
			"expireTime": 1_800_000_000,
			"chat": {"id": "7696", "name": "研究群", "userCount": "12"},
		})
		self.assertEqual(link_info(bridge, {"link": LINK}), {
			"status": "ok", "chat_id": "7696", "name": "研究群", "member_count": 12,
			"already_in_chat": True, "expire_time": 1_800_000_000,
		})
		self.assertEqual(bridge.client.calls, [("chats.PullChatLinkInfoRequest", {"token": TOKEN})])

	def test_join_failure_exposes_gateway_sid(self):
		bridge = FakeBridge({"sid": "you are already in target chat:7696", "payloadType": 4012})
		with self.assertRaisesRegex(GroupAdminError, "you are already in target chat:7696"):
			join_by_link(bridge, {"link": LINK})
		self.assertEqual(bridge.client.calls, [("chats.PutChatterViaLinkRequest", {"token": TOKEN})])

	def test_successful_join_drops_joined_groups_cache(self):
		bridge = FakeBridge({"chat": {"id": "7696", "name": "研究群"}})
		self.assertEqual(join_by_link(bridge, {"link": LINK}), {"status": "ok", "chat_id": "7696", "name": "研究群"})
		self.assertIsNone(bridge._joined_groups_cache)


if __name__ == "__main__":
	unittest.main()
