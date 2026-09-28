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
	def test_paper_command_lane_is_narrow(self):
		self.assertTrue(bridge.is_paper_command_message({"content": "收录 2609.30059v1"}))
		self.assertTrue(bridge.is_paper_command_message({"content": "收 1 3"}))
		self.assertTrue(bridge.is_paper_command_message({"content": "查询 KV cache"}))
		self.assertFalse(bridge.is_paper_command_message({"content": "收入增长 20%"}))
		self.assertFalse(bridge.is_paper_command_message({"content": "普通群消息"}))

	def test_xhs_command_lane_is_narrow(self):
		self.assertTrue(bridge.is_xhs_command_message({"content": "#xhs status"}))
		self.assertTrue(bridge.is_xhs_command_message({"content": "@_user_123 #xhs 最新"}))
		self.assertFalse(bridge.is_xhs_command_message({"content": "xhs status"}))
		self.assertFalse(bridge.is_xhs_command_message({"content": "#xhs"}))

	def test_xhs_command_lane_can_bind_chat_ids(self):
		self.assertTrue(bridge.is_xhs_command_message({"content": "#xhs status", "chat_id": "oc_bound"}))

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

	def test_private_tail_repair_starts_after_recovered_cursor(self):
		instance = object.__new__(bridge.Bridge)
		instance.private_tail_repair_enabled = True
		instance._private_tail_repair_in_flight = False
		instance.private_tail_repair_window = 16
		instance.private_gap_repair_chat_ids = set()
		instance.private_tail_repair_chat_ids = set()
		instance.websocket_chat_ids = {"7667390477875858612"}
		instance.event_spool = Mock()
		instance.event_spool.position_stats.return_value = {
			"7667390477875858612": {
				"last_position": 237,
				"last_recovered_position": 261,
			}
		}
		instance.private_tail_repair_count = 0
		instance.last_private_tail_repair_at = None
		instance.last_private_tail_repair_result = None
		calls = []

		async def repair(payload, *, reason):
			calls.append((payload, reason))
			return {
				"requested": 16,
				"recovered": 2,
				"forwarded": 2,
				"duplicates": 0,
				"filtered": 0,
				"failed": 0,
			}

		instance.repair_private_positions = repair
		result = asyncio.run(instance.repair_private_tail_once())

		self.assertEqual(result["recovered"], 2)
		self.assertEqual(calls, [(
			{"chat_id": "7667390477875858612", "start": 262, "end": 277},
			"periodic_private_tail_repair",
		)])
		self.assertEqual(instance.private_tail_repair_count, 1)


if __name__ == "__main__":
	unittest.main()
