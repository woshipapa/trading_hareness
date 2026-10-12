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


	def _binding_instance(self, directory, *, search, names, static=()):
		instance = bridge.Bridge.__new__(bridge.Bridge)
		instance.dynamic_routes = {}
		instance.static_listen_chats = set(static)
		instance.listen_chats = set(static)
		instance.websocket_chat_ids = {c for c in static if c.isdigit()}
		instance.chat_validation = {}
		instance.chat_stats = {}
		instance.route_bindings = {}
		instance.route_bindings_lock = threading.RLock()
		instance.route_bindings_path = Path(directory) / "route-bindings.json"
		instance.client = type("Client", (), {
			"search": lambda _self, query: search,
			"get_chat_info": lambda _self, chat_id: {"name": names.get(chat_id, "")},
		})()
		return instance

	def test_bind_route_resolves_websocket_id_from_the_typed_name_and_persists_it(self):
		with tempfile.TemporaryDirectory() as directory:
			instance = self._binding_instance(
				directory,
				search=[{"type": "group", "id": "9000000000000000010", "title": "<h>小杰夜报B</h>"}],
				names={"9000000000000000010": "小杰夜报B"},
			)
			result = instance.bind_route({"source_key": "relay_x", "chat_name": "小杰夜报B", "source_chat_id": "oc_demo"})

			self.assertEqual(result["status"], "bound")
			self.assertEqual(result["chat_id"], "9000000000000000010")
			self.assertIn("9000000000000000010", instance.websocket_chat_ids)
			self.assertEqual(instance.dynamic_routes["9000000000000000010"]["source_chat_id"], "oc_demo")
			reloaded = bridge.Bridge.__new__(bridge.Bridge)
			reloaded.route_bindings_path = instance.route_bindings_path
			self.assertEqual(reloaded._load_route_bindings()["relay_x"]["chat_id"], "9000000000000000010")

	def test_bind_route_takes_a_group_picked_by_id_and_still_verifies_its_name(self):
		from group_lookup import GroupLookupError
		with tempfile.TemporaryDirectory() as directory:
			# two groups share the name: a typed name is refused, a picked id is not
			instance = self._binding_instance(
				directory,
				search=[{"type": "group", "id": "1", "title": "同名"}, {"type": "group", "id": "2", "title": "同名"}],
				names={"1": "同名", "2": "同名"},
			)
			result = instance.bind_route({"source_key": "relay_x", "chat_name": "同名", "chat_id": "2"})
			self.assertEqual((result["status"], result["chat_id"]), ("bound", "2"))
			with self.assertRaises(GroupLookupError):
				instance.bind_route({"source_key": "relay_y", "chat_name": "别的名字", "chat_id": "1"})
			with self.assertRaises(ValueError):
				instance.bind_route({"source_key": "relay_y", "chat_name": "同名", "chat_id": "oc_1"})

	def test_bind_route_refuses_ambiguous_or_mismatched_names(self):
		from group_lookup import GroupLookupError
		with tempfile.TemporaryDirectory() as directory:
			twin = self._binding_instance(
				directory,
				search=[{"type": "group", "id": "1", "title": "同名"}, {"type": "group", "id": "2", "title": "同名"}],
				names={"1": "同名", "2": "同名"},
			)
			with self.assertRaises(GroupLookupError):
				twin.bind_route({"source_key": "relay_x", "chat_name": "同名"})
			self.assertFalse(twin.websocket_chat_ids)
			mismatch = self._binding_instance(
				directory,
				search=[{"type": "group", "id": "3", "title": "目标群"}],
				names={"3": "另一个群"},
			)
			with self.assertRaises(GroupLookupError):
				mismatch.bind_route({"source_key": "relay_x", "chat_name": "目标群"})
			self.assertFalse(mismatch.route_bindings)

	def test_rebinding_a_route_drops_the_old_subscription_but_never_an_env_owned_one(self):
		with tempfile.TemporaryDirectory() as directory:
			instance = self._binding_instance(
				directory, static={"9000000000000000099"},
				search=[{"type": "group", "id": "9000000000000000011", "title": "新群"}],
				names={"9000000000000000011": "新群"},
			)
			# the route was bound to a dashboard-added group first
			instance._register_runtime_route("9000000000000000020", {"source_key": "relay_x", "chat_name": "旧群"})
			instance.route_bindings["relay_x"] = {"source_key": "relay_x", "chat_id": "9000000000000000020", "chat_name": "旧群", "source_chat_id": ""}

			result = instance.bind_route({"source_key": "relay_x", "chat_name": "新群"})

			self.assertEqual(result["replaced_chat_id"], "9000000000000000020")
			self.assertNotIn("9000000000000000020", instance.websocket_chat_ids)
			self.assertIn("9000000000000000011", instance.websocket_chat_ids)
			# an env-owned chat survives an unbind of the route that points at it
			instance.route_bindings["relay_env"] = {"source_key": "relay_env", "chat_id": "9000000000000000099", "chat_name": "env群", "source_chat_id": ""}
			instance.unbind_route({"source_key": "relay_env"})
			self.assertIn("9000000000000000099", instance.websocket_chat_ids)

	def test_unbind_route_removes_the_persisted_mapping(self):
		with tempfile.TemporaryDirectory() as directory:
			instance = self._binding_instance(
				directory,
				search=[{"type": "group", "id": "9000000000000000012", "title": "临时群"}],
				names={"9000000000000000012": "临时群"},
			)
			instance.bind_route({"source_key": "relay_t", "chat_name": "临时群"})
			result = instance.unbind_route({"source_key": "relay_t"})

			self.assertEqual(result["status"], "unbound")
			self.assertNotIn("9000000000000000012", instance.websocket_chat_ids)
			reloaded = bridge.Bridge.__new__(bridge.Bridge)
			reloaded.route_bindings_path = instance.route_bindings_path
			self.assertNotIn("relay_t", reloaded._load_route_bindings())
			self.assertEqual(instance.unbind_route({"source_key": "relay_t"})["status"], "not_bound")


if __name__ == "__main__":
	unittest.main()
