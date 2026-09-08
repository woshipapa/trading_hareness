import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import itougu_neican_relay as relay


class ItouguRelayTests(unittest.TestCase):
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
