import unittest
import tempfile
from pathlib import Path

from operations import (
    MUTATING,
    PUBLIC_METHODS,
    OperationError,
    SpiderRuntime,
    capability_text,
    command_to_operation,
    format_result,
    sanitize,
)


class OperationContractTests(unittest.TestCase):
    def test_aliases_and_generic_json_share_one_contract(self):
        self.assertEqual(
            command_to_operation("#xhs search AI基础设施 5"),
            ("pc", "search_some_note", ["AI基础设施", 5], {}),
        )
        self.assertEqual(
            command_to_operation(
                '#xhs api pc.search_note {"args":["GPU"],"kwargs":{"page":1}}'
            ),
            ("pc", "search_note", ["GPU"], {"page": 1}),
        )
        self.assertEqual(
            command_to_operation(
                '#xhs creator.get_all_posted_notes {"args":[],"kwargs":{}}'
            ),
            ("creator", "get_all_posted_notes", [], {}),
        )
        self.assertEqual(
            command_to_operation("#xhs live.watch room-1 30"),
            ("live", "watch", ["room-1", 30], {}),
        )

    def test_public_registry_contains_all_runtime_namespaces(self):
        self.assertEqual(set(PUBLIC_METHODS), {"pc", "creator", "pgy", "qianfan", "live"})
        self.assertIn(("creator", "post_note"), MUTATING)
        self.assertIn("get_note_all_comment", PUBLIC_METHODS["pc"])
        self.assertIn("get_all_posted_notes", PUBLIC_METHODS["creator"])
        self.assertIn("get_user_fans", PUBLIC_METHODS["qianfan"])
        self.assertIn("choose_categories", PUBLIC_METHODS["pgy"])
        self.assertIn("watch", PUBLIC_METHODS["live"])

    def test_creator_metadata_helpers_read_only_from_media_inbox(self):
        with tempfile.TemporaryDirectory() as root:
            inbox = Path(root)
            media = inbox / "image.bin"
            media.write_bytes(b"media-bytes")

            class FakeCreator:
                def get_file_info(self, value, media_type="image"):
                    return value, media_type

                def upload_media(self, value, media_type):
                    return value, media_type

            runtime = SpiderRuntime(Path("/tmp/xhs-source"), Path("/tmp/xhs-cookie"))
            runtime._target = lambda namespace: (FakeCreator(), None)
            import operations
            previous = operations.ALLOWED_MEDIA_ROOT
            operations.ALLOWED_MEDIA_ROOT = inbox.resolve()
            try:
                self.assertEqual(
                    runtime.execute("creator", "get_file_info", [str(media)], {})[0],
                    b"media-bytes",
                )
                self.assertEqual(
                    runtime.execute("creator", "upload_media", [str(media), "image"], {"confirm": True})[0],
                    str(media.resolve()),
                )
            finally:
                operations.ALLOWED_MEDIA_ROOT = previous

    def test_sensitive_result_fields_are_redacted(self):
        value = sanitize({
            "web_session": "secret",
            "a1": "secret",
            "url": "https://x.test/note?xsec_token=secret",
            "title": "GPU systems",
        })
        self.assertEqual(value["web_session"], "[REDACTED]")
        self.assertEqual(value["a1"], "[REDACTED]")
        self.assertIn("[REDACTED]", value["url"])
        self.assertEqual(value["title"], "GPU systems")

    def test_help_and_invalid_json_are_safe(self):
        self.assertIn("pc:", capability_text())
        with self.assertRaises(OperationError):
            command_to_operation("#xhs api pc.search_note not-json")

    def test_result_is_bounded(self):
        text = format_result("pc.search_note", {"body": "x" * 100}, limit=50)
        self.assertLessEqual(len(text), 120)
        self.assertIn("结果已截断", text)


if __name__ == "__main__":
    unittest.main()
