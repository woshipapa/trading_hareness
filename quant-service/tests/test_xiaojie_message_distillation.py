"""Pure tests for the research-only Xiaojie message distiller."""

from __future__ import annotations

import json
import unittest

from app.xiaojie_message_distillation import distill_record, summarize_records


def _record(text: str, *, source_key: str = "relay_xiaojie", msg_type: str = "text"):
    return {
        "source_key": source_key,
        "source_chat_id": "chat-1",
        "source_message_id": "message-1",
        "source_create_time": 1_780_000_000_000,
        "message": {
            "msg_type": msg_type,
            "body": {"content": json.dumps({"text": text}, ensure_ascii=False)},
        },
    }


class XiaojieMessageDistillationTests(unittest.TestCase):
    def test_reply_keeps_available_time_and_separates_question_and_answer(self):
        row = _record(
            "【讲师】小杰\n回复 @用户：这个票是潜龙出海吗？：用户：\n--------\n"
            "符合模式，昨日标志K放量突破，今天回踩买点。前高有压力。\n"
            "—— 2026-09-17 11:08:59"
        )
        item = distill_record(row, known_names=("这个票",))
        self.assertEqual(item["role"], "instructor_reply")
        self.assertIn("潜龙出海", item["question_text"])
        self.assertIn("放量突破", item["answer_text"])
        self.assertEqual(item["available_at"], "2026-05-28T20:26:40+00:00")
        self.assertIn("qianlong", item["tags"])
        self.assertIn("volume_confirmation", item["tags"])
        self.assertIn("pressure_or_overhead", item["tags"])

    def test_available_time_prefers_first_ledger_receive_time(self):
        row = _record("一条消息")
        row["created_at"] = "2026-05-28T20:26:41+00:00"
        self.assertEqual(
            distill_record(row)["available_at"], "2026-05-28T20:26:41+00:00"
        )

    def test_non_text_is_explicitly_unavailable(self):
        item = distill_record(_record("ignored", msg_type="image"))
        self.assertEqual(item["content_status"], "unavailable_non_text")
        self.assertEqual(item["text"], None)
        self.assertEqual(item["tags"], [])

    def test_post_rich_text_is_flattened_without_image_resource_fields(self):
        row = _record("ignored", msg_type="post")
        row["message"]["body"]["content"] = json.dumps({
            "zh_cn": {"content": [[
                {"tag": "text", "text": "潜龙出海：放量突破"},
                {"tag": "img", "image_key": "secret-resource-key"},
            ]]}
        }, ensure_ascii=False)
        item = distill_record(row)
        self.assertEqual(item["content_status"], "parseable_rich_text")
        self.assertEqual(item["text"], "潜龙出海：放量突破")
        self.assertIn("qianlong", item["tags"])
        self.assertIn("volume_confirmation", item["tags"])

    def test_summary_does_not_count_message_body_author_time_as_available_time(self):
        rows = [
            _record("【讲师】小杰\n回复 @用户：请看中国巨石\n--------\n一成，5日线支撑。"),
            _record("图片", msg_type="image"),
        ]
        result = summarize_records(rows, known_names=("中国巨石",))
        self.assertEqual(result["records"], 2)
        self.assertEqual(result["parseable_text_records"], 1)
        self.assertEqual(result["roles"]["instructor_reply"], 1)
        self.assertEqual(result["content_status"]["unavailable_non_text"], 1)
        self.assertEqual(result["stock_mentions"]["中国巨石"], 1)
        self.assertEqual(result["available_at_semantics"], "created_at_fallback_source_create_time")


if __name__ == "__main__":
    unittest.main()
