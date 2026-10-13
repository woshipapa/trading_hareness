import unittest

from group_admin import GroupAdminError, checked, require_owner


class GroupAdminTest(unittest.TestCase):
	def test_require_owner_rejects_a_different_owner(self):
		with self.assertRaises(GroupAdminError):
			require_owner({"ownerId": "owner"}, "other")

	def test_checked_preserves_success_and_carries_gateway_sid(self):
		result = {"payloadType": 1, "cmd": 1}
		self.assertIs(checked(result), result)
		with self.assertRaisesRegex(GroupAdminError, "refused"):
			checked({"payloadType": 4012, "sid": "refused"})


if __name__ == "__main__":
	unittest.main()
