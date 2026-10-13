import unittest

from group_admin import GroupAdminError, checked, require_client, require_owner


class GroupAdminTest(unittest.TestCase):
	def test_require_owner_rejects_a_different_owner(self):
		with self.assertRaises(GroupAdminError):
			require_owner({"ownerId": "owner"}, "other")

	def test_require_client_reports_the_login_boundary(self):
		with self.assertRaisesRegex(RuntimeError, r"尚未登录\(needs_login\),无法读取群列表"):
			require_client(type("Bridge", (), {"client": None})())

	def test_checked_preserves_success_and_carries_gateway_sid(self):
		result = {"payloadType": 1, "cmd": 1}
		self.assertIs(checked(result), result)
		with self.assertRaisesRegex(GroupAdminError, "refused"):
			checked({"payloadType": 4012, "sid": "refused"})


if __name__ == "__main__":
	unittest.main()
