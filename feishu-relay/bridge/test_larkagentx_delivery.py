import json
import sys
import asyncio
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
	import bridge
except ModuleNotFoundError as error:
	bridge = None
	BRIDGE_IMPORT_ERROR = error
else:
	BRIDGE_IMPORT_ERROR = None


class FakeResponse(BytesIO):
	def __enter__(self):
		return self

	def __exit__(self, *_):
		self.close()


@unittest.skipUnless(bridge is not None, f"supervisor larkx dependency unavailable: {BRIDGE_IMPORT_ERROR}")
class LarkAgentXDeliveryTests(unittest.TestCase):
	def test_adapter_business_failure_is_retried(self):
		response = FakeResponse(json.dumps({"status": "failed", "message": "webhook temporary failure"}).encode("utf-8"))
		with patch.object(bridge, "urlopen", return_value=response):
			with self.assertRaisesRegex(RuntimeError, "adapter rejected event"):
				bridge.post_json("http://127.0.0.1:18300/internal/larkagentx/group-relay", "token", {"msg_id": "om_test"})

	def test_sent_and_filtered_adapter_results_are_accepted(self):
		for status in ("sent", "filtered", "duplicate", "processing"):
			with self.subTest(status=status):
				response = FakeResponse(json.dumps({"status": status}).encode("utf-8"))
				with patch.object(bridge, "urlopen", return_value=response):
					self.assertEqual(bridge.post_json("http://127.0.0.1:18300/internal/larkagentx/group-relay", "token", {"msg_id": "om_test"})["status"], status)

	def test_anqiang_source_keyword_is_filtered_before_adapter_request(self):
		instance = object.__new__(bridge.Bridge)
		instance.websocket_state = "connected"
		instance.websocket_chat_ids = {"7661209668907207659"}
		instance.dynamic_routes = {}
		instance.static_source_keys_by_chat = {"7661209668907207659": "anqiang"}
		instance.route_bindings = {}
		instance.anqiang_source_keys = {"anqiang"}
		instance.anqiang_block_keywords = {"般若星登山的川柏"}
		instance.chat_stats = {"7661209668907207659": {"observed_count": 0, "filtered_count": 0, "failed_count": 0, "self_message_count": 0}}
		instance.auth = Mock(user_id="another-user")
		instance.event_spool = Mock()
		instance.event_spool.record_position.return_value = None
		instance.event_spool.record_filtered.return_value = True
		instance.history_archive = Mock()
		instance.observed_count = 0
		instance.self_message_count = 0
		instance.failed_count = 0
		instance.forwarded_count = 0
		instance.retry_count = 0
		instance.last_observed_chat_id = ""
		instance.last_observed_message_type = ""
		message = {
			"msg_id": "ws-filtered-1",
			"chat_id": "7661209668907207659",
			"position": 42,
			"from_id": "member-1",
			"msg_type_name": "TEXT",
			"content": "般若星 登山的川柏",
		}
		with patch.object(bridge, "post_json") as post:
			result = asyncio.run(instance.on_message(message))
		self.assertEqual(result["status"], "filtered")
		post.assert_not_called()
		instance.event_spool.record_filtered.assert_called_once()
		instance.history_archive.append.assert_called_once()


if __name__ == "__main__":
	unittest.main()
