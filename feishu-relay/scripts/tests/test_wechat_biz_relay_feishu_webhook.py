import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load_relay(name):
    relay_path = Path(__file__).resolve().parents[1] / "wechat-biz-relay.py"
    spec = importlib.util.spec_from_file_location(name, relay_path)
    relay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay)
    return relay


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps(self._payload).encode()


class FeishuWebhookRoutingTests(unittest.TestCase):
    def test_webhook_map_parses_and_looks_up_by_chat_id(self):
        relay = _load_relay("wechat_biz_relay_webhook_map")
        with mock.patch.dict("os.environ", {"WECHAT_BIZ_FEISHU_WEBHOOKS": " oc_a = https://x/a ; oc_b=https://x/b"}):
            self.assertEqual(relay._feishu_webhook_url("oc_a"), "https://x/a")
            self.assertEqual(relay._feishu_webhook_url("oc_b"), "https://x/b")
            self.assertIsNone(relay._feishu_webhook_url("oc_unconfigured"))

    def test_post_via_webhook_nests_post_content_and_omits_uuid(self):
        relay = _load_relay("wechat_biz_relay_webhook_shape")
        captured = {}

        def fake_urlopen(req, timeout=20):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode())
            return _Resp({"code": 0})

        with mock.patch.object(relay.urllib.request, "urlopen", fake_urlopen):
            relay._post_via_feishu_webhook("https://x/hook", "标题", "正文内容")
        self.assertEqual(captured["url"], "https://x/hook")
        self.assertEqual(captured["body"]["msg_type"], "post")
        self.assertEqual(captured["body"]["content"]["post"]["title"], "标题")
        self.assertNotIn("uuid", captured["body"])
        self.assertNotIn("receive_id", captured["body"])

    def test_post_via_webhook_raises_on_nonzero_code(self):
        relay = _load_relay("wechat_biz_relay_webhook_error")
        with mock.patch.object(relay.urllib.request, "urlopen", lambda req, timeout=20: _Resp({"code": 19024, "msg": "keyword not found"})):
            with self.assertRaises(RuntimeError):
                relay._post_via_feishu_webhook("https://x/hook", "t", "c")

    def test_post_feishu_prefers_webhook_and_skips_token_fetch(self):
        relay = _load_relay("wechat_biz_relay_post_feishu_webhook")
        calls = {"webhook": 0, "tenant_api": 0, "token": 0}

        def fake_post_via_webhook(url, title, chunk):
            calls["webhook"] += 1

        def fake_token():
            calls["token"] += 1
            return "tok"

        def fake_urlopen(req, timeout=20):
            calls["tenant_api"] += 1
            raise AssertionError("must not call the tenant API when a webhook is configured")

        sink = {"chat_id": "oc_392b9a177d3539c35b8fc090d890ff0c", "name": "anqiang分享群1"}
        payload = {"text": "正文", "article_title": "标题", "message_id": "m1"}
        with mock.patch.dict("os.environ", {"WECHAT_BIZ_FEISHU_WEBHOOKS": "oc_392b9a177d3539c35b8fc090d890ff0c=https://x/hook"}), \
             mock.patch.object(relay, "feishu_tenant_token", fake_token), \
             mock.patch.object(relay, "_post_via_feishu_webhook", fake_post_via_webhook), \
             mock.patch.object(relay.urllib.request, "urlopen", fake_urlopen):
            result = relay.post_feishu(sink, payload)
        self.assertEqual(calls, {"webhook": 1, "tenant_api": 0, "token": 0})
        self.assertEqual(result["status"], "sent")

    def test_post_feishu_falls_back_to_tenant_api_when_no_webhook_configured(self):
        relay = _load_relay("wechat_biz_relay_post_feishu_tenant")
        calls = {"tenant_api": 0}

        def fake_urlopen(req, timeout=20):
            calls["tenant_api"] += 1
            return _Resp({"code": 0, "data": {"message_id": "om_1"}})

        sink = {"chat_id": "oc_570aeb3bbfb11fa2be66b25ca4568aad", "name": "公众号同步群"}
        payload = {"text": "正文", "article_title": "标题", "message_id": "m1"}
        with mock.patch.dict("os.environ", {"WECHAT_BIZ_FEISHU_WEBHOOKS": ""}), \
             mock.patch.object(relay, "feishu_tenant_token", lambda: "tok"), \
             mock.patch.object(relay.urllib.request, "urlopen", fake_urlopen):
            result = relay.post_feishu(sink, payload)
        self.assertEqual(calls["tenant_api"], 1)
        self.assertEqual(result["message_ids"], ["om_1"])


if __name__ == "__main__":
    unittest.main()
