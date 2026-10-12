import unittest

from chat_directory import groups_from_feed, membership_report, pull_joined_groups


class JoinedGroupsTest(unittest.TestCase):
	def test_only_live_group_chats_are_listed(self):
		chats = {
			"1": {"id": "7001", "type": 2, "name": " 小杰夜报B ", "memberCount": 4, "lastMessagePosition": 200},
			"2": {"id": "7002", "type": 1, "name": "某人"},
			"3": {"id": "7003", "type": 2, "name": "已解散", "isDissolved": True},
			"4": {"id": "oc_x", "type": 2, "name": "不是数字 ID"},
			"5": {"id": "7005", "type": "GROUP", "name": "枚举名", "userCount": "9"},
		}
		self.assertEqual(groups_from_feed(chats), [
			{"chat_id": "7001", "name": "小杰夜报B", "member_count": 4, "last_position": 200},
			{"chat_id": "7005", "name": "枚举名", "member_count": 9, "last_position": 0},
		])
		self.assertEqual(groups_from_feed(None), [])

	def test_both_feeds_are_paged_and_a_group_is_listed_once(self):
		pages = {
			("INBOX", None): {"cards": [1], "chats": {"a": {"id": "7001", "type": 2, "name": "乙群"}}, "nextCursor": 50},
			("INBOX", 50): {"cards": [1], "chats": {"b": {"id": "7002", "type": 2, "name": "甲群"}, "a": {"id": "7001", "type": 2, "name": "乙群"}}, "nextCursor": 20},
			("INBOX", 20): {"cards": [], "chats": {}, "nextCursor": 0},
			("DONE", None): {"cards": [1], "chats": {"c": {"id": "7003", "type": 2, "name": "归档群"}}, "nextCursor": 0},
		}
		asked = []
		def pull(feed, cursor):
			asked.append((feed, cursor))
			return pages[(feed, cursor)]
		groups = pull_joined_groups(pull)
		self.assertEqual([group["name"] for group in groups], ["乙群", "归档群", "甲群"])
		self.assertEqual(asked, [("INBOX", None), ("INBOX", 50), ("INBOX", 20), ("DONE", None)])

	def test_a_cursor_that_does_not_move_ends_the_paging(self):
		calls = []
		def pull(feed, cursor):
			calls.append(cursor)
			return {"cards": [1], "chats": {}, "nextCursor": 7}
		pull_joined_groups(pull, feeds=("INBOX",))
		self.assertEqual(calls, [None, 7])


class MembershipTest(unittest.TestCase):
	def test_member_left_dissolved_and_unknown(self):
		confirmed = []
		def confirm(chat_id):
			confirmed.append(chat_id)
			if chat_id == "7004":
				raise RuntimeError("gateway timeout")
			return chat_id == "7005"
		report = membership_report(
			["7001", "7002", "7003", "7004", "7005"],
			roles={"7001": 1, "7002": 2, "7003": 1, "7005": 0},
			infos={"7001": {"name": "在群", "firstChatMessagePosition": -1, "lastMessagePosition": 200, "memberCount": 4}, "7003": {"name": "散了", "isDissolved": True}},
			confirm_in_chat=confirm,
		)
		self.assertEqual(report["7001"], {"member": True, "role": 1, "dissolved": False, "name": "在群", "member_count": 4, "first_position": 1, "last_position": 200})
		self.assertIs(report["7002"]["member"], False)
		self.assertEqual((report["7003"]["member"], report["7003"]["dissolved"]), (False, True))
		# no role and a failed second check: unknown, not "left"
		self.assertIsNone(report["7004"]["member"])
		# the role list says not a member, the direct check says it is: trust the positive
		self.assertIs(report["7005"]["member"], True)
		# a MEMBER role needs no second call; a dissolved chat needs none either
		self.assertEqual(confirmed, ["7002", "7004", "7005"])


if __name__ == "__main__":
	unittest.main()
