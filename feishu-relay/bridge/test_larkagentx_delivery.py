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
		self.assertTrue(bridge.is_xhs_command_message({"content": "#xhs"}))

	def test_xhs_command_lane_can_bind_chat_ids(self):
		self.assertTrue(bridge.is_xhs_command_message({"content": "#xhs status", "chat_id": "oc_bound"}))

	def test_xhs_command_chats_are_part_of_the_effective_listener(self):
		self.assertEqual(
			bridge.effective_listen_chats(
				{"source-chat"}, {"summary-chat"},
				xhs_enabled=True, xhs_commands={"7690524560642280650"},
			),
			{"source-chat", "summary-chat", "7690524560642280650"},
		)
		self.assertNotIn(
			"7690524560642280650",
			bridge.effective_listen_chats(
				{"source-chat"}, set(),
				xhs_enabled=False, xhs_commands={"7690524560642280650"},
			),
		)

	def test_command_lane_uses_direct_adapter_ingress(self):
		instance = object.__new__(bridge.Bridge)
		instance.ingress_url = "http://127.0.0.1:18300/internal/larkagentx/group-relay"
		instance.command_ingress_url = "http://127.0.0.1:18300/internal/larkagentx/inbound"
		instance.summary_ingress_url = "http://127.0.0.1:18300/internal/larkagentx/summary"
		instance.summary_chat_ids = set()
		self.assertEqual(instance.ingress_url_for("7690524560642280650", command_lane=True), instance.command_ingress_url)
		self.assertEqual(instance.ingress_url_for("source-chat"), instance.ingress_url)

	def test_xhs_command_chat_ignores_non_commands_before_spooling(self):
		instance = object.__new__(bridge.Bridge)
		instance.websocket_state = "connecting"
		instance.paper_command_lane_enabled = True
		instance.xhs_command_lane_enabled = True
		instance.xhs_command_chat_ids = {"7690524560642280650"}
		instance.ignored_count = 0
		instance.event_spool = Mock()
		instance.event_spool.record_ignored.return_value = None
		instance._log_position_gap = Mock()
		message = {
			"msg_id": "webhook-summary-1",
			"chat_id": "7690524560642280650",
			"position": 101,
			"from_id": "webhook-bot",
			"msg_type_name": "TEXT",
			"content": "# AI Infra 每日情报摘要",
		}

		with patch.object(bridge, "post_json") as post:
			result = asyncio.run(instance.on_message(message))

		self.assertEqual(result["status"], "ignored")
		self.assertEqual(result["filter_reason"], "xhs_command_chat_non_command")
		self.assertEqual(instance.ignored_count, 1)
		instance.event_spool.record_ignored.assert_called_once_with(
			"7690524560642280650",
			position=101,
			message_id="webhook-summary-1",
			message_type="TEXT",
			reason="xhs_command_chat_non_command",
		)
		instance.event_spool.enqueue.assert_not_called()
		post.assert_not_called()

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
		instance.anqiang_chat_ids = set()
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

	def _summary_instance(self, *, summary_chat_ids):
		instance = object.__new__(bridge.Bridge)
		instance.websocket_state = "connected"
		instance.websocket_chat_ids = {"7661209668907207659", "7669744189332065542"}
		instance.summary_chat_ids = set(summary_chat_ids)
		instance.dynamic_routes = {}
		instance.static_source_keys_by_chat = {}
		instance.route_bindings = {}
		instance.anqiang_source_keys = set()
		instance.anqiang_chat_ids = set()
		instance.anqiang_block_keywords = set()
		instance.chat_stats = {}
		instance.auth = Mock(user_id="7000")
		instance.event_spool = Mock()
		instance.event_spool.record_position.return_value = None
		instance.event_spool.enqueue.return_value = "queued"
		instance.event_spool.claim.return_value = "claimed"
		instance.history_archive = Mock()
		instance.observed_count = instance.self_message_count = instance.failed_count = instance.forwarded_count = instance.retry_count = 0
		instance.last_observed_chat_id = instance.last_observed_message_type = ""
		instance.ingress_url = "http://adapter/inbound"
		instance.summary_ingress_url = "http://adapter/summary"
		instance.command_ingress_url = "http://adapter/command"
		instance.token = "t"
		return instance

	def test_own_message_is_an_echo_in_a_source_group_but_content_in_the_summary_group(self):
		instance = self._summary_instance(summary_chat_ids={"oc_summary", "7669744189332065542"})
		own = {"msg_id": "m-own", "from_id": "7000", "msg_type_name": "TEXT", "content": "#liwei 手工补充", "position": 5}
		with patch.object(bridge, "post_json", return_value={"status": "accepted"}) as post:
			echoed = asyncio.run(instance.on_message({**own, "chat_id": "7661209668907207659"}))
			self.assertEqual(echoed["status"], "filtered")
			post.assert_not_called()
			asyncio.run(instance.on_message({**own, "msg_id": "m-own-2", "chat_id": "7669744189332065542"}))
			post.assert_called_once()
			self.assertEqual(post.call_args.args[0], "http://adapter/summary")
		self.assertEqual(instance.self_message_count, 1)

	def test_a_post_title_travels_with_the_rendered_text(self):
		instance = self._summary_instance(summary_chat_ids=set())
		node = b"\n\x06\xe6\xad\xa3\xe6\x96\x87"  # TextProperty{content: "正文"}
		message = {"msg_id": "m-post", "chat_id": "7661209668907207659", "from_id": "8000", "msg_type_name": "POST", "content": "[富文本] 午评\n正文", "position": 6,
		           "content_data": {"title": "午评", "richText": {"elementIds": ["p"], "elements": {"dictionary": {"p": {"tag": 3, "property": b"", "childIds": ["t"]}, "t": {"tag": 1, "property": node}}}}}}
		with patch.object(bridge, "post_json", return_value={"status": "accepted"}) as post:
			asyncio.run(instance.on_message(message))
		sent = post.call_args.args[2]
		self.assertEqual(sent["_larkagentx_rich_text"], {"text": "正文", "links": [], "title": "午评"})

	def test_history_times_and_an_empty_pull_are_read_without_decoding(self):
		client = object.__new__(bridge.RecoveringLarkClient)
		asked = []
		def api(name, data):
			asked.append((name, data))
			return {"messages": {"101": {"position": 101, "createTime": 1791700000}, "103": {"createTime": "1791700120"}, "bad": {"createTime": 1}}} if data["positions"] else {}
		client.api = api
		self.assertEqual(sorted(client.history_times("769", 101, 104)), [(101, 1791700000.0), (103, 1791700120.0)])
		self.assertEqual(asked[0], ("messages.PullMessagesByPositionsRequest", {"chatId": "769", "positions": [101, 102, 103, 104]}))
		client.api = lambda name, data: {"messages": {}}
		self.assertEqual(client.pull_history("769", [1, 2]), [])

	def test_payload_extras_are_the_same_for_live_and_history_messages(self):
		node = b"\n\x06\xe6\xad\xa3\xe6\x96\x87"  # TextProperty{content: "正文"}
		href = b"\n\x13https://example.com"
		message = {"msg_id": "1", "msg_type_name": "TEXT", "content": "正文", "content_data": {"richText": {"elementIds": ["t", "a"], "elements": {"dictionary": {"t": {"tag": 1, "property": node}, "a": {"tag": 6, "property": href}}}}}}
		extras = bridge.payload_extras(message, "TEXT")
		self.assertEqual(extras, {"_larkagentx_rich_text": {"text": "正文https://example.com", "links": ["https://example.com"]}})
		self.assertEqual(bridge.payload_extras({"msg_id": "2", "content_data": {}}, "SYSTEM"), {})
		self.assertEqual(bridge.payload_extras({"msg_id": "3", "content_data": {"richText": None}}, "POST"), {})

	def test_history_export_is_limited_to_registered_chats_and_one_page(self):
		instance = self._summary_instance(summary_chat_ids=set())
		pulled = []
		instance.client = Mock()
		instance.client.pull_history.side_effect = lambda chat, positions: pulled.append((chat, positions)) or [{"msg_id": "9", "msg_type_name": "SYSTEM", "position": positions[0], "content": "x", "content_data": {"raw": b"\xff"}}]
		page = instance.history_page({"chat_id": "7661209668907207659", "start": 10, "end": 12})
		self.assertEqual(pulled, [("7661209668907207659", [10, 11, 12])])
		self.assertEqual(page["messages"][0]["content_data"], {"raw": "\ufffd"})
		for bad in ({"chat_id": "7000000000000000001", "start": 1, "end": 2}, {"chat_id": "oc_x", "start": 1, "end": 2},
		            {"chat_id": "7661209668907207659", "start": 1, "end": 500}, {"chat_id": "7661209668907207659", "start": 5, "end": 4}):
			with self.assertRaises(ValueError):
				instance.history_page(bad)
		instance.client.get_chat_info.return_value = {"name": "安强训练营1（50）", "firstChatMessagePosition": 1, "lastMessagePosition": 200}
		instance.client.history_times.side_effect = lambda chat, start, end: [(p, 1791700000 + p * 60) for p in range(start, end + 1)]
		located = instance.history_locate({"chat_id": "7661209668907207659", "from_time": 1791700000 + 50 * 60, "to_time": 1791700000 + 99 * 60})
		self.assertEqual((located["start"], located["end"], located["count"], located["chat_name"]), (50, 99, 50, "安强训练营1（50）"))

	def test_private_tail_repair_starts_after_recovered_cursor(self):
		instance = object.__new__(bridge.Bridge)
		instance.client = Mock()  # logged in; the tail pull is skipped when client is None
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
