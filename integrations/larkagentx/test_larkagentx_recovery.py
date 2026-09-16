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
		instance.reconnect_recovery_count = 0
		instance.recovery_reasons = []

		async def recover(reason):
			instance.recovery_reasons.append(reason)
			instance._recovery_in_flight = False

		instance.recover_gap = recover
		return instance

	async def test_first_connection_does_not_backfill(self):
		instance = self.make_bridge(1)
		instance.on_websocket_connected()
		await asyncio.sleep(0)
		self.assertEqual(instance.websocket_state, "connected")
		self.assertEqual(instance.reconnect_recovery_count, 0)
		self.assertEqual(instance.recovery_reasons, [])

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


if __name__ == "__main__":
	unittest.main()
