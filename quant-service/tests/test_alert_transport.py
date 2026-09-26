import asyncio
import unittest
from contextlib import asynccontextmanager
from unittest import mock

import httpx

from app import alert_transport

BOT_HOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/test"
ADAPTER = "http://adapter.local/alert"


class _Response:
    def __init__(self, payload, *, raises=None):
        self._payload, self._raises = payload, raises

    def raise_for_status(self):
        if self._raises is not None:
            raise self._raises

    def json(self):
        return self._payload


class _Client:
    """Records every post and replies from a scripted map of url -> response."""

    def __init__(self, replies):
        self.replies, self.posts = replies, []

    async def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        reply = self.replies[url]
        if isinstance(reply, Exception):
            raise reply
        return reply


def _factory(client):
    @asynccontextmanager
    async def factory():
        yield client

    return factory


class AlertTransportTests(unittest.TestCase):
    def _run(self, env, replies):
        client = _Client(replies)
        with mock.patch.dict(alert_transport.os.environ, env, clear=True), \
             mock.patch.object(alert_transport, "alert_http_client", _factory(client)):
            return asyncio.run(alert_transport.post_feishu_alert_text("买点提示")), client

    def test_the_bot_webhook_is_preferred_when_configured(self):
        result, client = self._run(
            {"QUANT_ALERT_FEISHU_WEBHOOK_URL": BOT_HOOK,
             "QUANT_ALERT_WEBHOOK_URL": ADAPTER, "QUANT_ALERT_WEBHOOK_TOKEN": "t"},
            {BOT_HOOK: _Response({"code": 0, "msg": "success"})},
        )
        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["transport"], "feishu_bot_webhook")
        self.assertEqual([url for url, _ in client.posts], [BOT_HOOK])
        self.assertEqual(client.posts[0][1]["json"], {"msg_type": "text", "content": {"text": "买点提示"}})

    def test_a_hook_that_answers_200_but_rejects_the_message_is_a_failure(self):
        # The hook returns 200 with code!=0 for a missing keyword or bad
        # signature, so the status line alone would report a false success.
        result, _client = self._run(
            {"QUANT_ALERT_FEISHU_WEBHOOK_URL": BOT_HOOK},
            {BOT_HOOK: _Response({"code": 19024, "msg": "Key Words Not Found"})},
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("Key Words Not Found", result["error"])

    def test_a_failing_hook_falls_through_to_the_adapter(self):
        result, client = self._run(
            {"QUANT_ALERT_FEISHU_WEBHOOK_URL": BOT_HOOK,
             "QUANT_ALERT_WEBHOOK_URL": ADAPTER, "QUANT_ALERT_WEBHOOK_TOKEN": "t"},
            {BOT_HOOK: httpx.ConnectError("hook unreachable"),
             ADAPTER: _Response({"ok": True})},
        )
        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["transport"], "adapter_webhook")
        self.assertEqual([url for url, _ in client.posts], [BOT_HOOK, ADAPTER])
        self.assertEqual(result["attempts"][0]["transport"], "feishu_bot_webhook")

    def test_only_the_adapter_configured_still_delivers(self):
        result, client = self._run(
            {"QUANT_ALERT_WEBHOOK_URL": ADAPTER, "QUANT_ALERT_WEBHOOK_TOKEN": "t"},
            {ADAPTER: _Response({"ok": True})},
        )
        self.assertEqual(result["status"], "sent")
        self.assertEqual([url for url, _ in client.posts], [ADAPTER])
        self.assertEqual(client.posts[0][1]["headers"], {"X-Quant-Alert-Token": "t"})

    def test_no_transport_configured_reports_disabled_not_failed(self):
        # The outbox retries a failure and stops retrying a disabled channel,
        # so an unconfigured peer must not accumulate doomed retries.
        result, client = self._run({}, {})
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(client.posts, [])

    def test_every_transport_failing_reports_failed_so_the_outbox_retries(self):
        result, _client = self._run(
            {"QUANT_ALERT_FEISHU_WEBHOOK_URL": BOT_HOOK,
             "QUANT_ALERT_WEBHOOK_URL": ADAPTER, "QUANT_ALERT_WEBHOOK_TOKEN": "t"},
            {BOT_HOOK: httpx.ConnectError("hook unreachable"),
             ADAPTER: httpx.ConnectError("adapter unreachable")},
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["attempts"]), 2)

    def test_the_direct_app_identity_sits_between_the_hook_and_the_adapter(self):
        sent = {}

        async def direct(text, **_kwargs):
            sent["text"] = text
            return {"status": "sent", "transport": "direct"}

        client = _Client({BOT_HOOK: httpx.ConnectError("hook unreachable")})
        env = {"QUANT_ALERT_FEISHU_WEBHOOK_URL": BOT_HOOK, "QUANT_FEISHU_DIRECT_ENABLED": "true",
               "FEISHU_APP_ID": "a", "FEISHU_APP_SECRET": "b", "FEISHU_ALERT_RECEIVE_ID": "oc_1",
               "QUANT_ALERT_WEBHOOK_URL": ADAPTER, "QUANT_ALERT_WEBHOOK_TOKEN": "t"}
        with mock.patch.dict(alert_transport.os.environ, env, clear=True), \
             mock.patch.object(alert_transport, "alert_http_client", _factory(client)), \
             mock.patch.object(alert_transport, "post_direct_feishu_alert_text", direct):
            result = asyncio.run(alert_transport.post_feishu_alert_text("买点提示"))
        self.assertEqual(result["transport"], "direct")
        self.assertEqual(sent["text"], "买点提示")
        self.assertEqual([url for url, _ in client.posts], [BOT_HOOK])

    def test_the_idempotency_key_reaches_the_adapter(self):
        client = _Client({ADAPTER: _Response({"ok": True})})
        with mock.patch.dict(alert_transport.os.environ, {"QUANT_ALERT_WEBHOOK_URL": ADAPTER, "QUANT_ALERT_WEBHOOK_TOKEN": "t"}, clear=True), \
             mock.patch.object(alert_transport, "alert_http_client", _factory(client)):
            asyncio.run(alert_transport.post_feishu_alert_text("买点提示", idempotency_key="delivery-1"))
        self.assertEqual(client.posts[0][1]["json"], {"text": "买点提示", "idempotency_key": "delivery-1"})

    def test_an_ambiguous_timeout_does_not_fall_through_to_another_group(self):
        # A read timeout may mean Feishu already posted the alert; trying the
        # adapter next would deliver it twice, possibly into a second group.
        result, client = self._run(
            {"QUANT_ALERT_FEISHU_WEBHOOK_URL": BOT_HOOK,
             "QUANT_ALERT_WEBHOOK_URL": ADAPTER, "QUANT_ALERT_WEBHOOK_TOKEN": "t"},
            {BOT_HOOK: httpx.ReadTimeout("no response"), ADAPTER: _Response({"ok": True})},
        )
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["ambiguous"])
        self.assertEqual([url for url, _ in client.posts], [BOT_HOOK])

    def test_the_direct_route_sends_the_key_as_feishu_uuid(self):
        from app import feishu_direct_alert

        class _TokenCache:
            async def token(self, _client, _config):
                return "tenant-token"

        api = "https://open.feishu.cn/open-apis/im/v1/messages"
        client = _Client({api: _Response({"code": 0, "data": {"message_id": "om_1"}})})
        env = {"QUANT_FEISHU_DIRECT_ENABLED": "true", "FEISHU_APP_ID": "a", "FEISHU_APP_SECRET": "b",
               "FEISHU_ALERT_RECEIVE_ID": "oc_1"}
        result = asyncio.run(feishu_direct_alert.post_direct_feishu_alert_text(
            "买点提示", idempotency_key="0f5d2c1e-delivery", environ=env,
            client_factory=_factory(client), token_cache=_TokenCache(),
        ))
        self.assertEqual(result["status"], "sent")
        self.assertEqual(client.posts[0][1]["json"]["uuid"], "0f5d2c1e-delivery")


if __name__ == "__main__":
    unittest.main()
