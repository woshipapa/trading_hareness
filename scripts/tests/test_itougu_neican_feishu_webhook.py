import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import itougu_neican_relay as relay


class FeishuWebhookRoutingTests(unittest.TestCase):
    def setUp(self):
        # The module-level parse cache must not leak between tests.
        relay._feishu_webhook_map_value = None
        relay._feishu_webhook_map_raw = None

    def test_webhook_map_parses_semicolon_separated_pairs(self):
        with mock.patch.dict("os.environ", {"ITOUGU_FEISHU_WEBHOOKS": " oc_a = https://x/a ; oc_b=https://x/b"}):
            self.assertEqual(relay._feishu_webhook_url("oc_a"), "https://x/a")
            self.assertEqual(relay._feishu_webhook_url("oc_b"), "https://x/b")
            self.assertIsNone(relay._feishu_webhook_url("oc_unconfigured"))

    def test_webhook_map_reparses_when_env_value_changes(self):
        with mock.patch.dict("os.environ", {"ITOUGU_FEISHU_WEBHOOKS": "oc_a=https://x/a"}):
            self.assertEqual(relay._feishu_webhook_url("oc_a"), "https://x/a")
        with mock.patch.dict("os.environ", {"ITOUGU_FEISHU_WEBHOOKS": "oc_a=https://x/a-v2"}):
            self.assertEqual(relay._feishu_webhook_url("oc_a"), "https://x/a-v2")

    def test_post_via_webhook_shapes_interactive_card_at_top_level(self):
        captured = {}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps({"code": 0}).encode()

        def fake_urlopen(req, timeout=20):
            captured["body"] = json.loads(req.data.decode())
            captured["url"] = req.full_url
            return _Resp()

        with mock.patch.object(relay.urllib.request, "urlopen", fake_urlopen):
            relay._post_via_feishu_webhook("https://x/hook", "interactive", {"schema": "2.0"})
        self.assertEqual(captured["url"], "https://x/hook")
        self.assertEqual(captured["body"], {"msg_type": "interactive", "card": {"schema": "2.0"}})
        self.assertNotIn("content", captured["body"])

    def test_post_via_webhook_shapes_post_under_content(self):
        captured = {}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps({"code": 0}).encode()

        def fake_urlopen(req, timeout=20):
            captured["body"] = json.loads(req.data.decode())
            return _Resp()

        with mock.patch.object(relay.urllib.request, "urlopen", fake_urlopen):
            relay._post_via_feishu_webhook("https://x/hook", "post", {"zh_cn": {"title": "t", "content": [[]]}})
        self.assertEqual(captured["body"], {"msg_type": "post", "content": {"post": {"zh_cn": {"title": "t", "content": [[]]}}}})

    def test_post_via_webhook_raises_on_nonzero_code(self):
        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                # 19024: message does not contain a required custom keyword.
                return json.dumps({"code": 19024, "msg": "keyword not found"}).encode()

        with mock.patch.object(relay.urllib.request, "urlopen", lambda req, timeout=20: _Resp()):
            with self.assertRaises(RuntimeError):
                relay._post_via_feishu_webhook("https://x/hook", "post", {"zh_cn": {}})

    def test_post_via_webhook_rejects_unsupported_msg_type(self):
        with self.assertRaises(RuntimeError):
            relay._post_via_feishu_webhook("https://x/hook", "text", {"text": "hi"})

    def test_send_feishu_prefers_webhook_over_tenant_api_when_configured(self):
        calls = {"webhook": 0, "tenant_api": 0}

        def fake_post_via_webhook(url, msg_type, content):
            calls["webhook"] += 1

        def fake_urlopen(req, timeout=20):
            calls["tenant_api"] += 1
            raise AssertionError("must not call the tenant API when a webhook is configured")

        with mock.patch.dict("os.environ", {"ITOUGU_FEISHU_WEBHOOKS": "oc_cf156f51d085e2c51bd66fda198b88a0=https://x/hook"}), \
             mock.patch.object(relay, "feishu_token", lambda: "tok"), \
             mock.patch.object(relay, "is_dedicated_destination", lambda chat_id: False), \
             mock.patch.object(relay, "_post_via_feishu_webhook", fake_post_via_webhook), \
             mock.patch.object(relay.urllib.request, "urlopen", fake_urlopen):
            relay.send_feishu("oc_cf156f51d085e2c51bd66fda198b88a0", "title", "body text", "dedup-seed")
        self.assertEqual(calls, {"webhook": 1, "tenant_api": 0})


if __name__ == "__main__":
    unittest.main()
