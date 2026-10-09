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


if __name__ == "__main__":
	unittest.main()
