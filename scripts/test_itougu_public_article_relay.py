import sys
import unittest
from unittest import mock
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

    def test_view_content_key_is_stable_when_view_id_changes(self):
        first = {"viewId": "v1", "content": "同一条公开观点"}
        second = {"viewId": "v2", "content": "同一条公开观点"}
        self.assertEqual(relay.view_content_key(first), relay.view_content_key(second))

    def test_same_article_with_new_view_id_is_sent_once(self):
        today = relay.datetime.now(relay.NEICAN.CST).strftime("%Y-%m-%d")
        rows = [
            {"viewId": "v1", "articleId": "a1", "publicTime": today + " 09:00:00"},
            {"viewId": "v2", "articleId": "a1", "publicTime": today + " 09:00:01"},
        ]
        article = {"isExist": 1, "circleId": "1661937625084334080", "title": "同一文章", "content": "正文"}
        with mock.patch.object(relay, "STATE_FILE", Path(self.id().replace(".", "_") + ".json")), \
             mock.patch.object(relay, "fetch_views", return_value=rows), \
             mock.patch.object(relay, "fetch_article", return_value=article), \
             mock.patch.object(relay.NEICAN, "send_feishu_many") as send:
            try:
                self.assertEqual(relay.poll_views(), 1)
                send.assert_called_once()
            finally:
                Path(relay.STATE_FILE).unlink(missing_ok=True)

if __name__ == "__main__":
    unittest.main()
