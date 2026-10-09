import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
	import bridge
except ModuleNotFoundError as error:
	bridge = None
	BRIDGE_IMPORT_ERROR = error
else:
	BRIDGE_IMPORT_ERROR = None


@unittest.skipUnless(bridge is not None, f"supervisor larkx dependency unavailable: {BRIDGE_IMPORT_ERROR}")
class WsParamOverrideTests(unittest.TestCase):
	def test_overrides_rewrite_declared_versions_and_keep_other_params(self):
		overrides = bridge.parse_ws_param_overrides(" sdk_version=7.93.0, lark_version=7.93.0 ,bad,=x,k= ")
		self.assertEqual(overrides, {"sdk_version": "7.93.0", "lark_version": "7.93.0"})
		url = ("wss://msg-frontier.feishu.cn/ws/v2?sdk_version=7.72.8&version_code=7.72.8"
		       "&lark_version=7.72.0&ticket=abc&device_id=d1")
		rewritten = bridge.apply_ws_param_overrides(url, overrides)
		self.assertIn("sdk_version=7.93.0", rewritten)
		self.assertIn("lark_version=7.93.0", rewritten)
		self.assertIn("version_code=7.72.8", rewritten)
		self.assertIn("ticket=abc", rewritten)
		self.assertIn("device_id=d1", rewritten)
		self.assertTrue(rewritten.startswith("wss://msg-frontier.feishu.cn/ws/v2?"))

	def test_empty_overrides_leave_the_url_untouched(self):
		url = "wss://example/ws?x=1"
		self.assertEqual(bridge.apply_ws_param_overrides(url, {}), url)
		self.assertEqual(bridge.parse_ws_param_overrides(""), {})

	def test_new_parameters_are_appended(self):
		rewritten = bridge.apply_ws_param_overrides(
			"wss://example/ws?x=1", {"fe_version": "9.9.9"})
		self.assertIn("x=1", rewritten)
		self.assertIn("fe_version=9.9.9", rewritten)


@unittest.skipUnless(bridge is not None, f"supervisor larkx dependency unavailable: {BRIDGE_IMPORT_ERROR}")
class RawFrameCaptureTests(unittest.TestCase):
	def test_captures_only_configured_chats_and_rotates(self):
		with tempfile.TemporaryDirectory() as tmp:
			capture = bridge.RawFrameCapture({"7684122107030031634"}, tmp, max_files=2)
			self.assertTrue(capture.enabled)
			other = [{"chat_id": "999", "msg_id": "m0", "msg_type_name": "TEXT"}]
			capture.capture(b"frame-zero", other)
			self.assertEqual(list(Path(tmp).glob("*.bin")), [])
			for index in range(3):
				matched = [{"chat_id": "7684122107030031634", "msg_id": f"m{index}", "msg_type_name": "CARD"}]
				capture.capture(f"frame-{index}".encode(), matched)
			bins = sorted(Path(tmp).glob("*.bin"))
			self.assertEqual(len(bins), 2)
			meta = json.loads(bins[-1].with_suffix(".json").read_text(encoding="utf-8"))
			self.assertEqual(meta["chat_ids"], ["7684122107030031634"])
			self.assertEqual(meta["message_types"], ["CARD"])

	def test_disabled_without_directory_or_chat_ids(self):
		self.assertFalse(bridge.RawFrameCapture(set(), "/tmp/x", 10).enabled)
		self.assertFalse(bridge.RawFrameCapture({"1"}, "", 10).enabled)

	def test_all_frames_mode_keeps_unexplained_frames_and_skips_understood_traffic(self):
		with tempfile.TemporaryDirectory() as tmp:
			capture = bridge.RawFrameCapture({"7684122107030031634"}, tmp, max_files=10, all_frames=True)
			self.assertTrue(capture.enabled)
			pad = b"x" * capture.MIN_UNEXPLAINED_FRAME_BYTES
			# Understood push traffic for another chat stays uncaptured.
			capture.capture(pad + b"-other", [{"chat_id": "999", "msg_id": "m0"}], cmd=6)
			# Tiny non-push control frames (acks/heartbeats) stay uncaptured.
			capture.capture(b"ack", [], cmd=4, reason="non_push_cmd")
			# The three unexplained shapes are kept with cmd and reason in meta.
			capture.capture(pad + b"-cmd9", [], cmd=9, reason="non_push_cmd")
			capture.capture(pad + b"-empty", [], cmd=6, reason="no_messages")
			capture.capture(pad + b"-broken", [], cmd=None, reason="decode_error")
			metas = sorted(Path(tmp).glob("*.json"))
			reasons = sorted(json.loads(item.read_text(encoding="utf-8"))["reason"] for item in metas)
			self.assertEqual(reasons, ["decode_error", "no_messages", "non_push_cmd"])
			cmd9 = next(json.loads(item.read_text(encoding="utf-8")) for item in metas
			            if json.loads(item.read_text(encoding="utf-8"))["reason"] == "non_push_cmd")
			self.assertEqual(cmd9["cmd"], 9)

	def test_all_frames_mode_defaults_off_and_watched_capture_is_unchanged(self):
		with tempfile.TemporaryDirectory() as tmp:
			capture = bridge.RawFrameCapture({"7684122107030031634"}, tmp, max_files=10)
			capture.capture(b"y" * 80, [], cmd=6, reason="no_messages")
			capture.capture(b"y" * 80, [], cmd=9, reason="non_push_cmd")
			self.assertEqual(list(Path(tmp).glob("*.bin")), [])
			capture.capture(b"watched", [{"chat_id": "7684122107030031634", "msg_id": "m1",
			                              "msg_type_name": "CARD"}], cmd=6)
			self.assertEqual(len(list(Path(tmp).glob("*.bin"))), 1)

	def test_all_frames_mode_alone_enables_capture(self):
		with tempfile.TemporaryDirectory() as tmp:
			self.assertTrue(bridge.RawFrameCapture(set(), tmp, 10, all_frames=True).enabled)

	def test_same_second_frames_keep_distinct_files(self):
		with tempfile.TemporaryDirectory() as tmp:
			capture = bridge.RawFrameCapture({"7684122107030031634"}, tmp, max_files=10)
			matched = [{"chat_id": "7684122107030031634", "msg_id": "m", "msg_type_name": "CARD"}]
			for index in range(3):
				capture.capture(f"same-second-{index}".encode(), matched)
			self.assertEqual(len(list(Path(tmp).glob("*.bin"))), 3)
			self.assertEqual(len(list(Path(tmp).glob("*.json"))), 3)


@unittest.skipUnless(bridge is not None, f"supervisor larkx dependency unavailable: {BRIDGE_IMPORT_ERROR}")
class CmdSetAndGatewayHeaderTests(unittest.TestCase):
	def test_cmd_set_parses_csv_and_keeps_default_on_garbage(self):
		self.assertEqual(bridge.parse_cmd_set("6,7001", frozenset({6})), frozenset({6, 7001}))
		self.assertEqual(bridge.parse_cmd_set(" 7001 ", frozenset()), frozenset({7001}))
		self.assertEqual(bridge.parse_cmd_set("", frozenset({6})), frozenset({6}))
		self.assertEqual(bridge.parse_cmd_set("x,,", frozenset({6})), frozenset({6}))

	def test_gateway_header_merge_is_case_insensitive_and_additive(self):
		merged = bridge.merge_gateway_headers(
			{"X-Web-Version": "3.9.32", "accept": "*/*"},
			{"x-web-version": "7.93.0", "x-new": "1"})
		self.assertEqual(merged["X-Web-Version"], "7.93.0")
		self.assertEqual(merged["accept"], "*/*")
		self.assertEqual(merged["x-new"], "1")
		self.assertNotIn("x-web-version", merged)


@unittest.skipUnless(bridge is not None, f"supervisor larkx dependency unavailable: {BRIDGE_IMPORT_ERROR}")
class NotifyFrameTests(unittest.TestCase):
	"""cmd 7001 notification frames: Packet.sid is the message id, payload
	f1.f1 the chat id (observed 2026-10-09, frame sha 6edaa5431561ef6a)."""

	@staticmethod
	def build_notify_frame(cmd=7001, message_id="7694598379343236301", chat_id="7685029453386222794"):
		from larkx.proto import proto_pb2 as P

		def varint_field(number, value):
			return bytes([number << 3]) + bytes([value])

		def bytes_field(number, payload):
			return bytes([(number << 3) | 2, len(payload)]) + payload

		body = (bytes_field(1, chat_id.encode()) + varint_field(2, 0)
			+ varint_field(3, 1) + varint_field(4, 0))
		packet = P.Packet()
		packet.cmd = cmd
		packet.payloadType = 1
		packet.sid = message_id
		packet.payload = bytes_field(1, body)
		frame = P.Frame()
		frame.service = 1
		frame.method = 1
		frame.payload = packet.SerializeToString()
		return frame.SerializeToString()

	def test_parses_the_observed_notification_shape(self):
		import proto_wire
		raw = self.build_notify_frame()
		notify = proto_wire.parse_notify_frame(raw)
		self.assertEqual(notify, {"cmd": 7001, "chat_id": "7685029453386222794",
		                          "message_id": "7694598379343236301"})

	def test_rejects_frames_without_a_numeric_chat_id(self):
		import proto_wire
		self.assertIsNone(proto_wire.parse_notify_frame(b"\x00\x01garbage"))
		raw = self.build_notify_frame(chat_id="oc_not_numeric")
		self.assertIsNone(proto_wire.parse_notify_frame(raw))

	def test_primary_decoder_gate_is_parameterized(self):
		import proto_wire
		raw = self.build_notify_frame()
		packet, messages = proto_wire.decode_primary_websocket(raw)
		self.assertEqual(int(packet.get("cmd")), 7001)
		self.assertEqual(messages, [])


@unittest.skipUnless(bridge is not None, f"supervisor larkx dependency unavailable: {BRIDGE_IMPORT_ERROR}")
class WsNotifyBridgeTests(unittest.IsolatedAsyncioTestCase):
	def make_bridge(self, allowed_chat="7684122107030031634"):
		import threading
		instance = bridge.Bridge.__new__(bridge.Bridge)
		instance.ws_notify_count = 0
		instance.last_ws_notify_at = None
		instance.last_ws_notify = None
		instance._notify_repair_in_flight = set()
		instance._private_repair_lock = threading.Lock()
		instance.private_gap_repair_enabled = True
		instance.repairs = []

		class _SpoolStub:
			def increment_counter(self, name, amount=1):
				return None

		instance.event_spool = _SpoolStub()
		instance._private_repair_allowed = lambda chat: chat == allowed_chat

		async def repair(chat_id, reason):
			instance.repairs.append((chat_id, reason))
			return {"recovered": 1, "forwarded": 1, "failed": 0}

		instance._repair_chat_tail = repair
		return instance

	async def test_notify_for_an_allowed_chat_pulls_its_tail_once(self):
		from unittest import mock
		instance = self.make_bridge()
		notify = {"cmd": 7001, "chat_id": "7684122107030031634", "message_id": "m1"}
		with mock.patch.object(bridge.asyncio, "sleep", new=mock.AsyncMock()):
			instance.on_ws_notify(notify)
			instance.on_ws_notify(notify)  # in-flight dedup: only one repair runs
			await asyncio.gather(*[task for task in asyncio.all_tasks()
			                       if task is not asyncio.current_task()])
		self.assertEqual(instance.repairs, [("7684122107030031634", "ws_notify_tail_repair")])
		self.assertEqual(instance.ws_notify_count, 2)
		self.assertEqual(instance.last_ws_notify["message_id"], "m1")
		self.assertEqual(instance._notify_repair_in_flight, set())

	async def test_notify_for_an_unknown_chat_is_counted_but_never_pulled(self):
		instance = self.make_bridge(allowed_chat="other")
		instance.on_ws_notify({"cmd": 7001, "chat_id": "999", "message_id": "m2"})
		await asyncio.sleep(0)
		self.assertEqual(instance.repairs, [])
		self.assertEqual(instance.ws_notify_count, 1)


@unittest.skipUnless(bridge is not None, f"supervisor larkx dependency unavailable: {BRIDGE_IMPORT_ERROR}")
class AuthStateMachineTests(unittest.TestCase):
	"""The bridge must survive a dead/expired session instead of crashing, so
	the QR login surface it hosts can recover it in place."""

	class _Expired(Exception):
		pass

	def make_bridge(self, build_outcomes):
		instance = bridge.Bridge.__new__(bridge.Bridge)
		instance.client = None
		instance.auth_state = "needs_login"
		instance.last_auth_error = None
		self._outcomes = list(build_outcomes)

		def build():
			outcome = self._outcomes.pop(0)
			if isinstance(outcome, Exception):
				raise outcome
			return outcome

		instance._build_client = build
		return instance

	def test_ensure_client_enters_needs_login_on_expiry_without_raising(self):
		import larkx.auth
		instance = self.make_bridge([larkx.auth.AuthExpired("session dead")])
		self.assertFalse(instance._ensure_client())
		self.assertIsNone(instance.client)
		self.assertEqual(instance.auth_state, "needs_login")
		self.assertIn("session dead", instance.last_auth_error)

	def test_ensure_client_recovers_once_credentials_are_valid(self):
		import larkx.auth
		sentinel = object()
		instance = self.make_bridge([larkx.auth.AuthExpired("dead"), sentinel])
		self.assertFalse(instance._ensure_client())
		self.assertTrue(instance._ensure_client())
		self.assertIs(instance.client, sentinel)
		self.assertEqual(instance.auth_state, "authed")
		self.assertIsNone(instance.last_auth_error)

	def test_ensure_client_is_a_noop_when_already_built(self):
		sentinel = object()
		instance = self.make_bridge([])
		instance.client = sentinel
		self.assertTrue(instance._ensure_client())
		self.assertIs(instance.client, sentinel)

	def test_auth_status_never_leaks_credential_values(self):
		import time as _time
		instance = bridge.Bridge.__new__(bridge.Bridge)
		instance.client = None
		instance.auth_state = "needs_login"
		instance.last_auth_error = None
		instance.auth = type("A", (), {"saved_at": _time.time() - 3600, "cookies": {"session": "SECRET"},
		                               "user_id": "u1"})()
		instance.qr_login = type("Q", (), {"stats": lambda _self: {"login_count": 0}})()
		status = instance.auth_status()
		self.assertEqual(status["state"], "needs_login")
		self.assertFalse(status["logged_in"])
		self.assertTrue(status["has_credentials"])
		self.assertEqual(status["user_id"], "u1")
		self.assertAlmostEqual(status["credential_age_hours"], 1.0, delta=0.2)
		self.assertNotIn("SECRET", json.dumps(status))


if __name__ == "__main__":
	unittest.main()
