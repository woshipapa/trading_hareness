import sys
import json
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import history_render as hr


def card_payload(dictionary, element_ids, *, from_id="u_card", create_time=1791521581):
    return {
        "msg_type_name": "CARD", "from_id": from_id, "create_time": create_time,
        "content": "[卡片]  ",
        "content_data": {"richtext": {"elementIds": element_ids,
                                      "elements": {"dictionary": dictionary}}},
    }


class CardRichtextTests(unittest.TestCase):
    def test_collects_text_leaves_in_child_order(self):
        # tag 3 = container (childIds), tag 1 = text leaf, tag 15 = markdown wrapper.
        dictionary = {
            "12": {"tag": 3, "childIds": ["3", "6"]},
            "3": {"tag": 3, "childIds": ["1"]},
            "1": {"tag": 1, "property": "第一段：板块分析"},
            "6": {"tag": 15, "childIds": ["4"]},
            "4": {"tag": 1, "property": "第二段：煤炭轮动"},
        }
        self.assertEqual(hr.render_card_richtext(card_payload(dictionary, ["12"])["content_data"]["richtext"]),
                         "第一段：板块分析\n第二段：煤炭轮动")

    def test_drops_the_client_upgrade_banner_and_keeps_nothing_from_it(self):
        dictionary = {
            "12": {"tag": 3, "childIds": ["5", "2"]},
            "5": {"tag": 15, "childIds": ["4"]},
            "4": {"tag": 1, "property": "\n5Upgrade to the latest app version to view the content"},
            "2": {"tag": 3, "childIds": ["1"]},
            "1": {"tag": 2, "property": "\x12\x30img_v3_whatever"},  # image leaf, no text
        }
        self.assertEqual(hr.render_card_richtext(card_payload(dictionary, ["12"])["content_data"]["richtext"]), "")

    def test_skips_anti_scrape_decoy_nodes(self):
        dictionary = {
            "12": {"tag": 3, "childIds": ["_2", "_4"]},
            "_2": {"tag": 1, "property": "EQr?icQ 诱饵乱码", "style": {"margin": "0px 0px -24px -99px"}},
            "_4": {"tag": 1, "property": "真实内容：临盘拉了欧盟反制"},
        }
        self.assertEqual(hr.render_card_richtext(card_payload(dictionary, ["12"])["content_data"]["richtext"]),
                         "真实内容：临盘拉了欧盟反制")

    def test_tolerates_cycles_and_missing_nodes(self):
        dictionary = {
            "a": {"tag": 3, "childIds": ["b"]},
            "b": {"tag": 3, "childIds": ["a", "c"]},  # cycle a→b→a
            "c": {"tag": 1, "property": "ok"},
        }
        self.assertEqual(hr.render_card_richtext(card_payload(dictionary, ["a"])["content_data"]["richtext"]), "ok")


class BannerAndCleanTests(unittest.TestCase):
    def test_banner_matcher_both_languages_and_prefix(self):
        self.assertTrue(hr.is_card_unavailable_notice("Upgrade to the latest app version to view the content"))
        self.assertTrue(hr.is_card_unavailable_notice("5Upgrade to the latest app version to view the content"))
        self.assertTrue(hr.is_card_unavailable_notice("请升级至最新版本客户端，以查看内容"))
        self.assertFalse(hr.is_card_unavailable_notice("临盘拉了欧盟反制"))

    def test_clean_strips_control_chars_and_private_cdn(self):
        self.assertEqual(hr.clean_text("\x00\x05正文 https://s1-imfile.feishucdn.com/static-resource/x 尾"),
                         "正文 尾")


class MessageTextTests(unittest.TestCase):
    def test_text_post_image_use_readable_forms(self):
        self.assertEqual(hr.message_text({"msg_type_name": "TEXT", "content": "[@] 续费私我"}), "[@] 续费私我")
        self.assertEqual(hr.message_text({"msg_type_name": "POST", "content": "[富文本] 下午轮动比较强"}),
                         "[富文本] 下午轮动比较强")
        self.assertEqual(hr.message_text({"msg_type_name": "IMAGE", "content": "[图片] imageKey=... 已加密"}), "[图片]")

    def test_image_only_card_renders_as_image(self):
        dictionary = {"12": {"tag": 3, "childIds": ["1"]}, "1": {"tag": 2, "property": "img"}}
        payload = card_payload(dictionary, ["12"])
        self.assertEqual(hr.message_text(payload), "[图片]")


class TranscriptTests(unittest.TestCase):
    def test_orders_by_time_groups_by_day_and_labels_speakers(self):
        rows = [
            {"msg_type_name": "TEXT", "from_id": "u1", "create_time": 1791528702, "content": "晚点的消息"},
            {"msg_type_name": "TEXT", "from_id": "u2", "create_time": 1791524041, "content": "早点的消息"},
            {"msg_type_name": "SYSTEM", "from_id": "1", "create_time": 1791524000, "content": "[系统消息] type=364"},
        ]
        out = hr.render_transcript(rows, name_map={"u1": "小杰", "u2": "Eric"})
        self.assertNotIn("系统消息", out)  # system dropped by default
        self.assertLess(out.index("早点的消息"), out.index("晚点的消息"))  # chronological
        self.assertIn("小杰: 晚点的消息", out)
        self.assertIn("Eric: 早点的消息", out)
        self.assertIn("== 2026-", out)  # day header present

    def test_accepts_export_rows_wrapping_payload(self):
        rows = [{"payload": {"msg_type_name": "TEXT", "from_id": "u1", "create_time": 1791524041, "content": "hi"}}]
        self.assertIn("u1: hi", hr.render_transcript(rows, include_date=False))

    def test_keeps_system_when_requested(self):
        rows = [{"msg_type_name": "SYSTEM", "from_id": "1", "create_time": 1791524000, "content": "[系统消息] 入群"}]
        self.assertIn("入群", hr.render_transcript(rows, drop_system=False))


class JsonCardTests(unittest.TestCase):
    """The archive's jsonCard carries the real content when richtext is only the upgrade banner."""

    BANNER = {"tag": "markdown", "property": {"elements": [{"tag": "plain_text", "property": {"content": "请升级至最新版本客户端，以查看内容"}}]}}

    def card(self, *new_body_elements):
        return {
            "body": {"elements": [{"tag": "column_set", "property": {"columns": [{"tag": "column", "property": {"elements": [
                {"tag": "img", "property": {"alt": {"tag": "plain_text", "property": {}}, "imageID": "16"}}, self.BANNER,
            ]}}]}}]},
            "newBody": {"tag": "body", "property": {"elements": [{"tag": "markdown", "property": {"elements": list(new_body_elements)}}]}},
            "schema": "2.0",
        }

    def test_link_only_card_keeps_its_link_and_drops_the_banner(self):
        link = {"tag": "link", "property": {"content": "http://liveplay.example.com/live/5060.m3u8", "url": {"url": "http://liveplay.example.com/live/5060.m3u8"}}}
        self.assertEqual(hr.render_json_card(json.dumps(self.card(link))), "http://liveplay.example.com/live/5060.m3u8")

    def test_labelled_link_keeps_label_and_target(self):
        text = {"tag": "plain_text", "property": {"content": "8点上课链接"}}
        link = {"tag": "link", "property": {"content": "点开观看", "url": {"url": "https://example.com/live"}}}
        self.assertEqual(hr.render_json_card(self.card(text, link)), "8点上课链接\n点开观看\nhttps://example.com/live")

    def test_decoy_and_malformed_input_are_ignored(self):
        decoy = {"tag": "div", "margin": "0px 0px -24px -99px", "text": {"tag": "plain_text", "content": "garbled"}}
        real = {"tag": "div", "text": {"tag": "plain_text", "content": "真内容"}}
        self.assertEqual(hr.render_json_card({"body": {"elements": [decoy, real]}}), "真内容")
        self.assertEqual(hr.render_json_card("{not json"), "")
        self.assertEqual(hr.render_json_card(None), "")

    def test_message_text_falls_back_to_json_card_when_richtext_is_only_the_banner(self):
        text = {"tag": "plain_text", "property": {"content": "8点上课链接。点开观看即可"}}
        payload = {"msg_type_name": "CARD", "content": "[卡片]", "content_data": {
            "richtext": {"elementIds": ["1"], "elements": {"dictionary": {"1": {"tag": 2, "property": "img"}}}},
            "jsonCard": json.dumps(self.card(text)),
        }}
        self.assertEqual(hr.message_text(payload), "8点上课链接。点开观看即可")

    def test_image_only_card_without_json_text_still_renders_as_image(self):
        payload = {"msg_type_name": "CARD", "content": "[卡片]", "content_data": {
            "richtext": {"elementIds": ["1"], "elements": {"dictionary": {"1": {"tag": 2, "property": "img"}}}},
            "jsonCard": json.dumps(self.card()),
        }}
        self.assertEqual(hr.message_text(payload), "[图片]")


if __name__ == "__main__":
    unittest.main()
