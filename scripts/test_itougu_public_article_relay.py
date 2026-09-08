import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import itougu_public_article_relay as relay


class PublicArticleRelayTests(unittest.TestCase):
    def test_view_stub_article_id_is_resolved(self):
        view = {"content": '{"subtype":"1","articleId":"2097155426675281920","title":"每日精选录0908"}'}
        self.assertEqual(relay.view_article_id(view), "2097155426675281920")

    def test_article_formatter_uses_body(self):
        title, text = relay.format_article(
            "研习社",
            {"publicTime": "2026-09-08 10:49:52", "title": "每日精选录0908", "content": "<p>正文内容</p>"},
            "",
            "轮询",
        )
        self.assertIn("每日精选录0908", text)
        self.assertIn("正文内容", text)
        self.assertTrue(title.startswith("[轮询] 研习社"))

    def test_main_behavior_views_are_bounded_into_one_digest(self):
        digest = relay.build_main_behavior_digest([
            {"view_id": "v1", "title": "观点一", "text": "第一条"},
            {"view_id": "v2", "title": "观点二", "text": "第二条"},
        ], delivery_label="轮询")
        self.assertEqual(digest["view_ids"], ["v1", "v2"])
        self.assertTrue(digest["title"].startswith("[轮询] 主力行为学"))
        self.assertIn("观点一", digest["text"])
        self.assertIn("观点二", digest["text"])


if __name__ == "__main__":
    unittest.main()
