import json
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":
	unittest.main()
