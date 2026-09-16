import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
	import bridge
except ModuleNotFoundError as error:  # The workstation may not carry the supervisor venv.
	bridge = None
	BRIDGE_IMPORT_ERROR = error
else:
	BRIDGE_IMPORT_ERROR = None


@unittest.skipUnless(bridge is not None, f"supervisor larkx dependency unavailable: {BRIDGE_IMPORT_ERROR}")
class LarkAgentXRecoveryTests(unittest.IsolatedAsyncioTestCase):
	def make_bridge(self, attempts):
		instance = bridge.Bridge.__new__(bridge.Bridge)
		instance.websocket_attempt_count = attempts
		instance.websocket_state = "connecting"
		instance._recovery_in_flight = False
		instance.startup_recovery_count = 0
		instance.reconnect_recovery_count = 0
		instance.partial_recovery_count = 0
		instance.decode_fallback_count = 0
		instance.last_decode_fallback_at = None
		instance.unknown_field_count = 0
		instance.wire_mismatch_count = 0
		instance.partial_frame_count = 0
		instance.partial_entry_error_count = 0
		instance.groups_skipped = 0
		instance.last_protocol_telemetry = None
		instance.recovery_reasons = []

		async def recover(reason):
			instance.recovery_reasons.append(reason)
			instance._recovery_in_flight = False

		instance.recover_gap = recover
		return instance

	async def test_first_connection_repairs_bounded_startup_gap(self):
		instance = self.make_bridge(1)
		instance.on_websocket_connected()
		await asyncio.sleep(0)
		self.assertEqual(instance.websocket_state, "connected")
		self.assertEqual(instance.startup_recovery_count, 1)
		self.assertEqual(instance.recovery_reasons, ["larkagentx_websocket_startup"])

	async def test_reconnect_schedules_bounded_history_repair(self):
		instance = self.make_bridge(2)
		instance.on_websocket_connected()
		await asyncio.sleep(0)
		self.assertEqual(instance.reconnect_recovery_count, 1)
		self.assertEqual(instance.recovery_reasons, ["larkagentx_websocket_reconnect"])

	async def test_reconnect_does_not_overlap_existing_repair(self):
		instance = self.make_bridge(3)
		instance._recovery_in_flight = True
		instance.on_websocket_connected()
		await asyncio.sleep(0)
		self.assertEqual(instance.reconnect_recovery_count, 0)
		self.assertEqual(instance.recovery_reasons, [])

	async def test_partial_frame_triggers_official_repair(self):
		instance = self.make_bridge(1)
		instance.on_decode_fallback(
			ValueError("generated schema rejected frame"),
			128,
			1,
			{"partial": True, "unknown_fields": {}, "wire_mismatches": {}, "entry_errors": [{"layer": "entity"}], "groups_skipped": 0, "field_fingerprint": "deadbeefdeadbeef"},
		)
		await asyncio.sleep(0)
		self.assertEqual(instance.partial_recovery_count, 1)
		self.assertEqual(instance.recovery_reasons, ["larkagentx_partial_frame"])


if __name__ == "__main__":
	unittest.main()
