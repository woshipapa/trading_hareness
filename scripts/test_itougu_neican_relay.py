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

    def test_delivery_wraps_only_named_product_destinations(self):
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
                self.assertEqual([call[0] for call in calls], [["shared"], [dedicated_id]])
                self.assertNotIn(relay.PRODUCT_NOTICE, calls[0][2])
                self.assertEqual(calls[1][2].count(relay.PRODUCT_NOTICE), 2)
                self.assertIn("正文内容", calls[1][2])
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
