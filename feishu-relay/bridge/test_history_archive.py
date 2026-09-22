import json
import tempfile
import unittest
from pathlib import Path

from history_archive import HistoryArchive


class HistoryArchiveTests(unittest.TestCase):
	def test_append_is_idempotent_and_redacts_media_secrets(self):
		with tempfile.TemporaryDirectory() as directory:
			archive = HistoryArchive(Path(directory) / "history.db")
			payload = {
				"chat_id": "7684122107030031634", "msg_id": "m1", "msg_type_name": "IMAGE",
				"create_time": 1789951119, "_larkagentx_image": {"image_id": "img_1", "key_hex": "a" * 64, "iv_hex": "b" * 24},
			}
			self.assertTrue(archive.append("event-1", payload))
			self.assertFalse(archive.append("event-1", payload))
			row = archive.export_rows("7684122107030031634")[0]
			stored = row["payload"]
			self.assertEqual(stored["_larkagentx_image"]["key_hex"], "[redacted]")
			self.assertEqual(stored["_larkagentx_image"]["iv_hex"], "[redacted]")

	def test_incremental_cursor_and_time_window(self):
		with tempfile.TemporaryDirectory() as directory:
			archive = HistoryArchive(Path(directory) / "history.db")
			for index, create_time in enumerate((10, 20, 30), start=1):
				archive.append(f"event-{index}", {"chat_id": "cat", "msg_id": f"m-{index}", "msg_type": "TEXT", "create_time": create_time})
			rows = archive.export_rows("cat", from_time=20, to_time=30, after_sequence=1)
			self.assertEqual([row["message_id"] for row in rows], ["m-2", "m-3"])
			self.assertEqual(archive.stats("cat"), {"count": 3, "latest_sequence": 3})


if __name__ == "__main__":
	unittest.main()
