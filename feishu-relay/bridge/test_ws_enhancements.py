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


if __name__ == "__main__":
	unittest.main()
