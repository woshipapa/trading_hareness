import os
import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import itougu_neican_relay as relay


class ItouguRelayTests(unittest.TestCase):
    def test_static_watermark_asset_is_a_nonempty_png(self):
        payload = relay.WATERMARK_IMAGE_FILE.read_bytes()
        self.assertTrue(payload.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(payload), 1024)

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

    def test_product_card_can_carry_the_static_image_watermark(self):
        card = relay.build_product_card("尾盘掘金内参", "正文", image_key="img_watermark", image_alt="momo W1-demo")
        self.assertEqual(card["body"]["elements"][0], {
            "tag": "img",
            "img_key": "img_watermark",
            "alt": {"tag": "plain_text", "content": "momo W1-demo"},
        })
        self.assertEqual(card["body"]["elements"][1]["tag"], "div")

    def test_send_uses_card_v2_only_for_dedicated_product_group(self):
        originals = {
            "chat_ids": relay.CHAT_IDS,
            "juejin": relay.JUEJIN_CHAT_IDS,
            "qinlong": relay.QINLONG_CHAT_IDS,
            "token": relay.feishu_token,
            "urlopen": relay.urllib.request.urlopen,
            "image_key": relay._watermark_image_key,
        }
        relay.CHAT_IDS = ["shared"]
        relay.JUEJIN_CHAT_IDS = ["juejin-only"]
        relay.QINLONG_CHAT_IDS = []
        calls = []

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
            if request.full_url.endswith("/im/v1/images"):
                return Response(b'{"code":0,"data":{"image_key":"img_watermark"}}')
            calls.append(json.loads(request.data.decode("utf-8")))
            return Response(b'{"code":0}')

        relay.feishu_token = lambda: "unit-token"
        relay.urllib.request.urlopen = fake_urlopen
        relay._watermark_image_key = None
        try:
            relay.send_feishu("juejin-only", "尾盘掘金内参", "正文", "append-1")
            relay.send_feishu("shared", "尾盘掘金内参", "正文", "append-1")
        finally:
            relay.CHAT_IDS = originals["chat_ids"]
            relay.JUEJIN_CHAT_IDS = originals["juejin"]
            relay.QINLONG_CHAT_IDS = originals["qinlong"]
            relay.feishu_token = originals["token"]
            relay.urllib.request.urlopen = originals["urlopen"]
            relay._watermark_image_key = originals["image_key"]
        self.assertEqual(calls[0]["msg_type"], "interactive")
        card = json.loads(calls[0]["content"])
        self.assertEqual(card["schema"], "2.0")
        self.assertEqual(card["body"]["elements"][0]["img_key"], "img_watermark")
        self.assertIn("momo W1-", card["body"]["elements"][0]["alt"]["content"])
        self.assertEqual(calls[1]["msg_type"], "post")

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
