import os
import json
import importlib.util
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import itougu_neican_relay as relay


class ItouguRelayTests(unittest.TestCase):
    def test_dedicated_chat_ids_exclude_shared_destination(self):
        original_chat_ids = relay.CHAT_IDS
        original_juejin = relay.JUEJIN_CHAT_IDS
        original_qinlong = relay.QINLONG_CHAT_IDS
        relay.CHAT_IDS = ["shared"]
        relay.JUEJIN_CHAT_IDS = ["shared", "juejin-only"]
        relay.QINLONG_CHAT_IDS = ["shared", "qinlong-only"]
        try:
            self.assertEqual(relay.dedicated_chat_ids_for_product("1806593447818383361"), ["juejin-only"])
            self.assertEqual(relay.dedicated_chat_ids_for_product("1661993558510538753"), ["qinlong-only"])
            self.assertEqual(relay.dedicated_chat_ids_for_product("unknown"), [])
        finally:
            relay.CHAT_IDS = original_chat_ids
            relay.JUEJIN_CHAT_IDS = original_juejin
            relay.QINLONG_CHAT_IDS = original_qinlong

    def test_product_notice_wraps_message_with_exact_prefix_and_suffix(self):
        body = "正文内容"
        wrapped = relay.wrap_product_message(body)
        expected = "%s\n\n%s\n\n%s" % (
            relay.PRODUCT_NOTICE,
            body,
            relay.PRODUCT_NOTICE,
        )
        self.assertEqual(wrapped, expected)

    def test_message_notice_is_selected_by_final_destination(self):
        originals = relay.CHAT_IDS, relay.JUEJIN_CHAT_IDS, relay.QINLONG_CHAT_IDS
        old_secret = os.environ.get("ITOUGU_WATERMARK_SECRET")
        os.environ["ITOUGU_WATERMARK_SECRET"] = "unit-test-watermark-secret"
        relay.CHAT_IDS = ["shared"]
        relay.JUEJIN_CHAT_IDS = ["juejin-only"]
        relay.QINLONG_CHAT_IDS = ["qinlong-only"]
        try:
            self.assertEqual(relay.message_for_destination("shared", "正文"), "正文")
            self.assertEqual(relay.message_for_destination("juejin-only", "正文").count(relay.PRODUCT_NOTICE), 2)
            self.assertEqual(relay.message_for_destination("qinlong-only", "正文").count(relay.PRODUCT_NOTICE), 2)
        finally:
            relay.CHAT_IDS, relay.JUEJIN_CHAT_IDS, relay.QINLONG_CHAT_IDS = originals
            if old_secret is None:
                os.environ.pop("ITOUGU_WATERMARK_SECRET", None)
            else:
                os.environ["ITOUGU_WATERMARK_SECRET"] = old_secret

    def test_product_card_uses_adapter_compatible_schema_2(self):
        card = relay.build_product_card("尾盘掘金内参 · 续 2/2", "正文\n动态水印 W1-1234567890abcdef")
        self.assertEqual(card["schema"], "2.0")
        self.assertFalse(card["config"]["enable_forward_interaction"])
        self.assertEqual(card["header"]["title"]["tag"], "plain_text")
        self.assertEqual(card["body"]["direction"], "vertical")
        self.assertEqual(card["body"]["elements"][0]["tag"], "div")
        self.assertEqual(card["body"]["elements"][0]["text"]["tag"], "plain_text")
        self.assertIn("动态水印 W1-1234567890abcdef", card["body"]["elements"][0]["text"]["content"])

    def test_product_card_can_carry_image_only_body(self):
        card = relay.build_product_card("尾盘掘金内参", None, image_key="img_watermark", image_alt="momo W1-demo")
        self.assertEqual(card["body"]["elements"][0], {
            "tag": "img",
            "img_key": "img_watermark",
            "alt": {"tag": "plain_text", "content": "momo W1-demo"},
        })
        self.assertEqual(len(card["body"]["elements"]), 1)

    def test_link_post_uses_explicit_clickable_anchor(self):
        post = relay.build_link_post("午盘链接", "请打开 https://example.com/detail?id=1。")
        elements = post["zh_cn"]["content"][0]
        self.assertEqual(elements[0], {"tag": "text", "text": "请打开 "})
        self.assertEqual(elements[1], {
            "tag": "a", "text": "https://example.com/detail?id=1",
            "href": "https://example.com/detail?id=1",
        })
        self.assertEqual(elements[2], {"tag": "text", "text": "。"})

    def test_inline_video_json_link_stops_at_m3u8(self):
        body = ('{"publishTime":"2026-09-14 12:21:30",'
                '"videoUrl":"https://voss.itougu.com/video/demo.m3u8",'
                '"videoName":"【午盘分析】-9月14日","videoId":"2099346824640024576"}')
        elements = relay.build_link_post("尾盘掘金", body)["zh_cn"]["content"][0]
        anchor = next(item for item in elements if item.get("tag") == "a")
        self.assertEqual(anchor["text"], "https://voss.itougu.com/video/demo.m3u8")
        self.assertEqual(anchor["href"], "https://voss.itougu.com/video/demo.m3u8")
        self.assertIn('"videoName"', "".join(item.get("text", "") for item in elements if item.get("tag") == "text"))

    def test_video_metadata_is_extracted_from_flat_or_escaped_payload(self):
        item = {
            "content": r'{"videoUrl":"https:\/\/voss.itougu.com\/video\/demo.m3u8?token=x","videoName":"午盘分析","videoId":"id-after-url"}'
        }
        self.assertEqual(
            relay.video_line(item),
            "〔复盘视频〕午盘分析：https://voss.itougu.com/video/demo.m3u8",
        )
        self.assertNotIn("id-after-url", relay.video_line(item))

    def test_flat_video_url_is_used_by_video_task(self):
        task = relay.video_task({
            "videoUrl": "https://voss.itougu.com/video/flat.m3u8?x=1",
            "videoName": "午盘分析",
            "videoId": "flat-id",
        }, "尾盘掘金内参")
        self.assertEqual(task["url"], "https://voss.itougu.com/video/flat.m3u8")

    def test_external_links_are_classified_as_text_messages(self):
        self.assertTrue(relay.contains_external_link("午盘链接 https://example.com/detail?id=1"))
        self.assertTrue(relay.contains_external_link("晚盘入口 www.example.com"))
        self.assertFalse(relay.contains_external_link("推荐股票：示例股份"))

    def test_image_body_removes_only_outer_notice_and_marker_lines(self):
        wrapped = relay.wrap_product_message("🟢 推荐股票", "W1-0123456789abcdef")
        self.assertEqual(relay.image_body_text(wrapped), "🟢 推荐股票")
        body_with_notice = "正文\n\n%s\n\n结尾" % relay.PRODUCT_NOTICE
        self.assertEqual(relay.image_body_text(body_with_notice), body_with_notice)

    def test_image_shop_watermark_is_standalone(self):
        self.assertEqual(relay.IMAGE_SHOP_WATERMARK, "咸鱼店铺：餐厅焦糖味的momo")
        self.assertNotIn(relay.PRODUCT_NOTICE, relay.IMAGE_SHOP_WATERMARK)

    def test_emoji_runs_keep_symbols_and_zwj_sequences_together(self):
        self.assertEqual(
            relay._split_emoji_runs("时间 🕐 状态 🟢👨‍💻"),
            [("时间 ", False), ("🕐", True), (" 状态 ", False), ("🟢👨‍💻", True)],
        )

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow is provisioned on edge, not required on every dev host")
    def test_rendered_product_image_contains_the_full_body_and_fingerprint(self):
        font_candidates = (
            relay.WATERMARK_FONT_FILE,
            Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        )
        font_path = next((path for path in font_candidates if path.is_file()), None)
        if font_path is None:
            self.skipTest("no CJK font available on this host")
        old_font = relay.WATERMARK_FONT_FILE
        relay.WATERMARK_FONT_FILE = font_path
        try:
            image = relay.render_product_image("尾盘掘金内参", "正文\n认真一手咸鱼店铺：餐厅焦糖味的momo", "W1-demo")
        finally:
            relay.WATERMARK_FONT_FILE = old_font
        self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(image), 1024)

    def test_send_no_longer_renders_image_cards_for_dedicated_groups(self):
        """2026-09: dedicated destinations dropped the watermarked image card;
        every destination now sends plain post/text, and the image
        rendering/upload machinery is never invoked from send_feishu."""
        originals = {
            "chat_ids": relay.CHAT_IDS,
            "juejin": relay.JUEJIN_CHAT_IDS,
            "qinlong": relay.QINLONG_CHAT_IDS,
            "token": relay.feishu_token,
            "urlopen": relay.urllib.request.urlopen,
            "render": relay.render_product_image,
            "upload": relay._upload_product_image,
        }
        relay.CHAT_IDS = ["shared"]
        relay.JUEJIN_CHAT_IDS = ["juejin-only"]
        relay.QINLONG_CHAT_IDS = []
        calls = []
        render_calls = []

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return self.payload

        def fake_urlopen(request, timeout=20):
            calls.append(json.loads(request.data.decode("utf-8")))
            return Response(b'{"code":0}')

        relay.feishu_token = lambda: "unit-token"
        relay.urllib.request.urlopen = fake_urlopen
        relay.render_product_image = lambda title, text, marker: (render_calls.append((title, text, marker)) or b"\x89PNG\r\n\x1a\nunit-image")
        relay._upload_product_image = lambda token, image: "img_watermark"
        try:
            relay.send_feishu("juejin-only", "尾盘掘金内参", "正文", "append-1")
            relay.send_feishu("shared", "尾盘掘金内参", "正文", "append-1")
            relay.send_feishu("juejin-only", "午盘链接", "请打开 https://example.com/detail", "append-link")
        finally:
            relay.CHAT_IDS = originals["chat_ids"]
            relay.JUEJIN_CHAT_IDS = originals["juejin"]
            relay.QINLONG_CHAT_IDS = originals["qinlong"]
            relay.feishu_token = originals["token"]
            relay.urllib.request.urlopen = originals["urlopen"]
            relay.render_product_image = originals["render"]
            relay._upload_product_image = originals["upload"]
        self.assertEqual([call["msg_type"] for call in calls], ["post", "post", "post"])
        self.assertEqual(render_calls, [])
        dedicated_post = json.loads(calls[0]["content"])
        dedicated_elements = dedicated_post["zh_cn"]["content"][0]
        self.assertIn("正文", "".join(e.get("text", "") for e in dedicated_elements))
        link_post = json.loads(calls[2]["content"])
        link_elements = link_post["zh_cn"]["content"][0]
        link_anchor = next(element for element in link_elements if element.get("tag") == "a")
        self.assertEqual(link_anchor["href"], "https://example.com/detail")

    def test_dynamic_watermark_is_stable_per_message_and_destination(self):
        old_secret = os.environ.get("ITOUGU_WATERMARK_SECRET")
        os.environ["ITOUGU_WATERMARK_SECRET"] = "unit-test-watermark-secret"
        try:
            first = relay.watermark_id("juejin-only", "正文", "append-1")
            same = relay.watermark_id("juejin-only", "正文", "append-1")
            other_chat = relay.watermark_id("qinlong-only", "正文", "append-1")
            other_message = relay.watermark_id("juejin-only", "正文", "append-2")
            self.assertRegex(first, r"^W1-[0-9a-f]{16}$")
            self.assertEqual(first, same)
            self.assertNotEqual(first, other_chat)
            self.assertNotEqual(first, other_message)
        finally:
            if old_secret is None:
                os.environ.pop("ITOUGU_WATERMARK_SECRET", None)
            else:
                os.environ["ITOUGU_WATERMARK_SECRET"] = old_secret

    def test_every_dedicated_chunk_keeps_dynamic_watermark(self):
        old_secret = os.environ.get("ITOUGU_WATERMARK_SECRET")
        old_chat_ids = relay.CHAT_IDS
        old_juejin = relay.JUEJIN_CHAT_IDS
        os.environ["ITOUGU_WATERMARK_SECRET"] = "unit-test-watermark-secret"
        relay.CHAT_IDS = ["shared"]
        relay.JUEJIN_CHAT_IDS = ["juejin-only"]
        try:
            # 2026-09: dedicated destinations no longer render an image, so
            # chunking uses the full text budget (MAX_FEISHU_TEXT_CHARS), not
            # the tighter size that used to keep a rendered image legible.
            chunks = relay.message_chunks_for_destination(
                "juejin-only", "x" * (relay.MAX_FEISHU_TEXT_CHARS + 1), "append-1")
            self.assertEqual(len(chunks), 2)
            self.assertTrue(all(chunk.count("动态水印 W1-") == 2 for chunk in chunks))
            self.assertTrue(all(len(chunk) <= relay.MAX_FEISHU_TEXT_CHARS for chunk in chunks))
        finally:
            relay.CHAT_IDS = old_chat_ids
            relay.JUEJIN_CHAT_IDS = old_juejin
            if old_secret is None:
                os.environ.pop("ITOUGU_WATERMARK_SECRET", None)
            else:
                os.environ["ITOUGU_WATERMARK_SECRET"] = old_secret

    def test_delivery_fans_out_to_shared_and_named_destination(self):
        originals = {
            "chat_ids": relay.CHAT_IDS,
            "juejin": relay.JUEJIN_CHAT_IDS,
            "qinlong": relay.QINLONG_CHAT_IDS,
            "load_headers": relay.load_headers,
            "fetch_append": relay.fetch_append,
            "load_state": relay.load_state,
            "save_state": relay.save_state,
            "send_feishu_many": relay.send_feishu_many,
        }
        relay.CHAT_IDS = ["shared"]
        relay.JUEJIN_CHAT_IDS = ["juejin-only"]
        relay.QINLONG_CHAT_IDS = ["qinlong-only"]
        relay.load_headers = lambda: {}
        relay.fetch_append = lambda business_id, headers: [{
            "appendContentId": "a1",
            "publishTime": "2026-09-08 15:00:00",
            "content": "<p>正文内容</p>",
        }]
        relay.load_state = lambda: {"seen": {}}
        relay.save_state = lambda state: None
        calls = []
        relay.send_feishu_many = lambda chat_ids, title, text, dedup_seed: calls.append(
            (list(chat_ids), title, text, dedup_seed))
        try:
            for business_id, dedicated_id in (
                ("1806593447818383361", "juejin-only"),
                ("1661993558510538753", "qinlong-only"),
            ):
                calls.clear()
                relay._deliver_new_unlocked(
                    products={business_id: relay.WATCH[business_id]}, verbose=False)
                self.assertEqual([call[0] for call in calls], [["shared", dedicated_id]])
                self.assertNotIn(relay.PRODUCT_NOTICE, calls[0][2])
                self.assertIn("正文内容", calls[0][2])
        finally:
            relay.CHAT_IDS = originals["chat_ids"]
            relay.JUEJIN_CHAT_IDS = originals["juejin"]
            relay.QINLONG_CHAT_IDS = originals["qinlong"]
            relay.load_headers = originals["load_headers"]
            relay.fetch_append = originals["fetch_append"]
            relay.load_state = originals["load_state"]
            relay.save_state = originals["save_state"]
            relay.send_feishu_many = originals["send_feishu_many"]

    def test_public_sync_test_override_never_includes_product_specific_chat(self):
        product = next(iter(relay.WATCH))
        self.assertEqual(relay.chat_ids_for_product(product, override=["public-sync"]), ["public-sync"])

    def test_process_test_destination_overrides_all_fanout(self):
        original = relay.TEST_CHAT_IDS
        relay.TEST_CHAT_IDS = ["public-sync"]
        try:
            self.assertEqual(relay.chat_ids_for_product("1806593447818383361"), ["public-sync"])
            self.assertEqual(relay.article_chat_ids(), ["public-sync"])
        finally:
            relay.TEST_CHAT_IDS = original

    def test_default_product_routing_keeps_public_sync_and_only_opt_in_dedicated_destinations(self):
        product = "1806593447818383361"  # 尾盘掘金内参
        original = relay.JUEJIN_CHAT_IDS
        relay.JUEJIN_CHAT_IDS = ["juejin-only"]
        try:
            destinations = relay.chat_ids_for_product(product)
        finally:
            relay.JUEJIN_CHAT_IDS = original
        self.assertIn(relay.CHAT_IDS[0], destinations)
        self.assertIn("juejin-only", destinations)

    def test_parallel_fetch_keeps_network_bound_work_bounded_and_ordered(self):
        def fetch(value):
            time.sleep(0.12)
            return value.upper()

        started = time.monotonic()
        result = relay.parallel_fetch(["a", "b", "c"], fetch, max_workers=3)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.25, f"independent fetches were serialized: {elapsed:.3f}s")
        self.assertEqual(result, [("a", "A"), ("b", "B"), ("c", "C")])

    def test_fanout_is_bounded_and_concurrent(self):
        original = relay.send_feishu
        calls = []

        def fake_send(chat_id, title, text, dedup_seed):
            time.sleep(0.12)
            calls.append(chat_id)

        relay.send_feishu = fake_send
        try:
            started = time.monotonic()
            relay.send_feishu_many(["chat-a", "chat-b", "chat-c"], "title", "body", "id")
            elapsed = time.monotonic() - started
        finally:
            relay.send_feishu = original
        self.assertLess(elapsed, 0.25, f"fan-out was serialized: {elapsed:.3f}s")
        self.assertEqual(set(calls), {"chat-a", "chat-b", "chat-c"})


if __name__ == "__main__":
    unittest.main()
