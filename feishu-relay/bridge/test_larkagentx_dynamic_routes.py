import asyncio
import json
import sys
import tempfile
import threading
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
	import bridge
except ModuleNotFoundError as error:  # The workstation may not carry the supervisor venv.
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
class LarkAgentXDynamicRouteTests(unittest.TestCase):
	def test_route_catalog_is_loaded_without_secrets(self):
		instance = bridge.Bridge.__new__(bridge.Bridge)
		instance.dynamic_route_discovery = True
		instance.route_catalog_url = "http://127.0.0.1:18300/internal/larkagentx/routes"
		instance.token = "test-token"
		instance.route_catalog = []
		instance.route_catalog_last_refresh_at = None
		instance.route_catalog_error = None
		instance.route_bindings = {}
		payload = {"routes": [
			{"source_key": "relay_demo", "chat_name": "调研纪要 市场消息", "source_chat_id": "oc_source", "tag": "diaoyan", "enabled": True},
			{"source_key": "disabled", "chat_name": "停用群", "source_chat_id": "oc_disabled", "enabled": False},
		]}
		with patch.object(bridge, "urlopen", return_value=FakeResponse(json.dumps(payload).encode("utf-8"))) as open_mock:
			instance.refresh_route_catalog()
		open_mock.assert_called_once()
		request = open_mock.call_args.args[0]
		self.assertEqual(request.get_header("X-larkagentx-token"), "test-token")
		self.assertEqual(instance.route_catalog, [{"source_key": "relay_demo", "chat_name": "调研纪要 市场消息", "source_chat_id": "oc_source"}])
		self.assertIsNone(instance.route_catalog_error)

	def test_route_catalog_keeps_pending_name_only_route(self):
		instance = bridge.Bridge.__new__(bridge.Bridge)
		instance.dynamic_route_discovery = True
		instance.route_catalog_url = "http://127.0.0.1:18300/internal/larkagentx/routes"
		instance.token = "test-token"
		instance.route_catalog = []
		instance.route_catalog_last_refresh_at = None
		instance.route_catalog_error = None
		instance.route_bindings = {}
		payload = {"routes": [{"source_key": "relay_pending", "chat_name": "马安强VIP高端训练营", "source_chat_id": ""}]}
		with patch.object(bridge, "urlopen", return_value=FakeResponse(json.dumps(payload).encode("utf-8"))):
			instance.refresh_route_catalog()
		self.assertEqual(instance.route_catalog, [{"source_key": "relay_pending", "chat_name": "马安强VIP高端训练营", "source_chat_id": ""}])

	def test_unknown_numeric_chat_is_bound_by_unique_name(self):
		with tempfile.TemporaryDirectory() as directory:
			instance = bridge.Bridge.__new__(bridge.Bridge)
			instance.dynamic_route_discovery = True
			instance.route_catalog_url = "http://127.0.0.1:18300/internal/larkagentx/routes"
			instance.route_catalog = [{"source_key": "relay_demo", "chat_name": "调研纪要 市场消息", "source_chat_id": "oc_source"}]
			instance.route_catalog_error = None
			instance.dynamic_routes = {}
			instance.listen_chats = set()
			instance.websocket_chat_ids = set()
			instance.chat_validation = {}
			instance.chat_stats = {}
			instance.route_bindings = {}
			instance.route_bindings_lock = threading.RLock()
			instance.route_bindings_path = Path(directory) / "route-bindings.json"
			instance.client = type("Client", (), {"get_chat_info": lambda _self, _chat_id: {"name": "调研纪要 市场消息"}})()
			instance.refresh_route_catalog = lambda: None

			route = asyncio.run(instance.discover_dynamic_route("9000000000000000001"))

			self.assertEqual(route["source_key"], "relay_demo")
			self.assertEqual(instance.dynamic_routes["9000000000000000001"], route)
			self.assertIn("9000000000000000001", instance.websocket_chat_ids)
			self.assertEqual(instance.chat_validation["9000000000000000001"]["state"], "verified")
			self.assertEqual(instance.route_bindings["relay_demo"]["chat_id"], "9000000000000000001")

	def test_duplicate_group_name_fails_closed(self):
		instance = bridge.Bridge.__new__(bridge.Bridge)
		instance.dynamic_route_discovery = True
		instance.route_catalog_url = "http://127.0.0.1:18300/internal/larkagentx/routes"
		instance.route_catalog = [
			{"source_key": "one", "chat_name": "重复群", "source_chat_id": "oc_one"},
			{"source_key": "two", "chat_name": "重复群", "source_chat_id": "oc_two"},
		]
		instance.route_catalog_error = None
		instance.dynamic_routes = {}
		instance.listen_chats = set()
		instance.websocket_chat_ids = set()
		instance.chat_validation = {}
		instance.chat_stats = {}
		instance.client = type("Client", (), {"get_chat_info": lambda _self, _chat_id: {"name": "重复群"}})()
		instance.refresh_route_catalog = lambda: None

		route = asyncio.run(instance.discover_dynamic_route("9000000000000000002"))

		self.assertIsNone(route)
		self.assertNotIn("9000000000000000002", instance.websocket_chat_ids)
		self.assertIn("群名重复", instance.route_catalog_error)

	def test_dynamic_binding_is_persisted_for_restart(self):
		with tempfile.TemporaryDirectory() as directory:
			instance = bridge.Bridge.__new__(bridge.Bridge)
			instance.route_bindings_path = Path(directory) / "route-bindings.json"
			instance.route_bindings_lock = threading.RLock()
			instance.route_bindings = {}
			route = {
				"source_key": "relay_persisted",
				"chat_name": "持久化测试群",
				"source_chat_id": "",
			}
			instance._persist_route_binding("9000000000000000003", route)

			reloaded = bridge.Bridge.__new__(bridge.Bridge)
			reloaded.route_bindings_path = instance.route_bindings_path
			bindings = reloaded._load_route_bindings()
			self.assertEqual(bindings["relay_persisted"]["chat_id"], "9000000000000000003")
			self.assertEqual(bindings["relay_persisted"]["chat_name"], "持久化测试群")


if __name__ == "__main__":
	unittest.main()
