import unittest

from group_admin import GroupAdminError, checked, require_client, require_owner

try:
	import requests
	from larkx.proto import proto_pb2
except ModuleNotFoundError as error:
	GATEWAY_IMPORT_ERROR = error
	requests = None
	proto_pb2 = None
else:
	GATEWAY_IMPORT_ERROR = None


class GroupAdminTest(unittest.TestCase):
	def test_gateway_error_uses_decoded_sid_or_http_status(self):
		from group_admin import gateway_error

		response = type("Response", (), {"content": b"body", "status_code": 400})()
		packet = type("Packet", (), {"sid": "you are already in target chat:769"})()
		def bad_decoder(body):
			raise ValueError("bad")

		self.assertEqual(str(gateway_error(response, lambda body: packet)), "you are already in target chat:769")
		self.assertEqual(str(gateway_error(response, lambda body: packet, ("769",))), "you are already in target chat:[已隐藏]")
		self.assertEqual(str(gateway_error(response, lambda body: type("Packet", (), {})())), "网关返回 HTTP 400")
		self.assertEqual(str(gateway_error(response, bad_decoder)), "网关返回 HTTP 400")

	def test_require_owner_rejects_a_different_owner(self):
		with self.assertRaises(GroupAdminError):
			require_owner({"ownerId": "owner"}, "other")

	def test_require_client_reports_the_login_boundary(self):
		with self.assertRaisesRegex(RuntimeError, r"尚未登录\(needs_login\),无法读取群列表"):
			require_client(type("Bridge", (), {"client": None})())

	@unittest.skipUnless(requests is not None, f"gateway dependencies unavailable: {GATEWAY_IMPORT_ERROR}")
	def test_require_client_translates_refused_gateway_command(self):
		packet = proto_pb2.Packet(sid="you are already in target chat:769")
		response = type("Response", (), {"content": packet.SerializeToString(), "status_code": 400})()

		class Client:
			def api(self, *args, **kwargs):
				raise requests.HTTPError(response=response)

		with self.assertRaisesRegex(GroupAdminError, r"you are already in target chat:\[已隐藏\]"):
			require_client(type("Bridge", (), {"client": Client()})()).api("chats.PutChatterViaLinkRequest", {"token": "769"})

	def test_checked_preserves_success_and_carries_gateway_sid(self):
		result = {"payloadType": 1, "cmd": 1}
		self.assertIs(checked(result), result)
		with self.assertRaisesRegex(GroupAdminError, "refused"):
			checked({"payloadType": 4012, "sid": "refused"})


if __name__ == "__main__":
	unittest.main()
