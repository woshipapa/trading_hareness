import http.cookiejar
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

# edge_api creates its process-wide Store at import time. Point that production
# initialization path at an isolated writable directory before importing it.
IMPORT_STATE = tempfile.TemporaryDirectory()
os.environ["XHS_STATE_DIR"] = IMPORT_STATE.name

import edge_api
from collector import normalize
from store import Store


class DashboardHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous_store = edge_api.STORE
        self.previous_token = edge_api.TOKEN
        self.previous_runtime = edge_api.RUNTIME
        self.previous_webhook = edge_api.FEISHU_WEBHOOK
        edge_api.STORE = Store(Path(self.tmp.name) / "state.db")
        edge_api.TOKEN = "test-collector-token"
        edge_api.FEISHU_WEBHOOK = "https://example.invalid/hook"
        self.server = edge_api.ThreadingHTTPServer(("127.0.0.1", 0), edge_api.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.jar = http.cookiejar.CookieJar()
        self.browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        edge_api.STORE = self.previous_store
        edge_api.TOKEN = self.previous_token
        edge_api.RUNTIME = self.previous_runtime
        edge_api.FEISHU_WEBHOOK = self.previous_webhook
        self.tmp.cleanup()

    def browser_json(self, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST" if payload is not None else "GET",
        )
        with self.browser.open(request, timeout=3) as response:
            return response.status, json.load(response)

    def test_dashboard_bootstraps_an_http_only_session(self):
        with self.browser.open(self.base + "/xhs/", timeout=3) as response:
            page = response.read().decode()
            self.assertEqual(response.status, 200)
            self.assertIn("XHS Intelligence", page)
            self.assertIn("HttpOnly", response.headers["Set-Cookie"])
            self.assertIn("SameSite=Strict", response.headers["Set-Cookie"])
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        status, payload = self.browser_json("/v1/dashboard")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertIn("topics", payload)

    def test_dashboard_source_respects_the_strict_style_policy(self):
        source = (edge_api.DASHBOARD_ROOT / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('style="', source)
        self.assertIn("safeXhsUrl", source)

    def test_machine_api_still_requires_the_collector_token(self):
        request = urllib.request.Request(self.base + "/v1/status")
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=3)
        self.assertEqual(error.exception.code, 401)
        error.exception.close()

    def test_dashboard_session_cannot_invoke_arbitrary_spider_operations(self):
        self.browser.open(self.base + "/xhs/", timeout=3).close()
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.browser_json("/v1/operation", {"namespace": "pc", "method": "search_note"})
        self.assertEqual(error.exception.code, 401)
        error.exception.close()

    def test_dashboard_operation_uses_the_same_allowlist_and_redaction(self):
        class FakeRuntime:
            def execute(self, namespace, method, args, kwargs):
                self.call = (namespace, method, args, kwargs)
                return {"title": "GPU serving", "web_session": "private"}

        runtime = FakeRuntime()
        edge_api.RUNTIME = runtime
        self.browser.open(self.base + "/xhs/", timeout=3).close()
        status, payload = self.browser_json("/v1/dashboard/operation", {
            "operation": "pc.search_note", "args": ["GPU"], "kwargs": {},
        })
        self.assertEqual(status, 200)
        self.assertEqual(runtime.call, ("pc", "search_note", ["GPU"], {}))
        self.assertEqual(payload["result"]["web_session"], "[REDACTED]")
        _, capabilities = self.browser_json("/v1/capabilities")
        self.assertTrue(any(row["name"] == "pc" for row in capabilities["namespaces"]))

    def test_feishu_message_submission_is_idempotent(self):
        self.browser.open(self.base + "/xhs/", timeout=3).close()
        payload = {"text": "人工复核完成", "request_id": "request-123"}
        first_status, first = self.browser_json("/v1/dashboard/feishu/messages", payload)
        second_status, second = self.browser_json("/v1/dashboard/feishu/messages", payload)
        self.assertEqual(first_status, 202)
        self.assertEqual(second_status, 202)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        _, delivery = self.browser_json("/v1/feishu/status")
        self.assertEqual(len(delivery["deliveries"]), 1)
        self.assertNotIn("人工复核完成", json.dumps(delivery, ensure_ascii=False))

    def test_dashboard_can_manage_watch_users_without_exposing_a_secret(self):
        self.browser.open(self.base + "/xhs/", timeout=3).close()
        status, payload = self.browser_json("/v1/dashboard/watch-users", {
            "action": "add", "user_id": "user-123456", "label": "Systems author",
        })
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "updated")
        _, listing = self.browser_json("/v1/watch-users?enabled=all")
        self.assertEqual(listing["users"][0]["user_id"], "user-123456")
        self.assertNotIn("test-collector-token", json.dumps(listing))

    def test_recommendation_items_are_projected_for_the_dashboard(self):
        note = normalize({
            "id": "a" * 24,
            "note_card": {"title": "GPU serving", "desc": "KV cache", "user": {"nickname": "Infra"}},
        }, "recommendation:test")
        edge_api.STORE.add_note(note, "recommendation:test")
        edge_api.STORE.create_recommendation_run("test-run", requested=1)
        edge_api.STORE.add_recommendation_item("test-run", note, 1)
        self.browser.open(self.base + "/xhs/", timeout=3).close()
        _, payload = self.browser_json("/v1/recommendations/items?run_id=test-run")
        item = payload["items"][0]
        self.assertEqual(item["title"], "GPU serving")
        self.assertEqual(item["url"], "https://www.xiaohongshu.com/explore/" + "a" * 24)
        self.assertNotIn("body", item)
        self.assertNotIn("xsec_token", json.dumps(item))


if __name__ == "__main__":
    unittest.main()
