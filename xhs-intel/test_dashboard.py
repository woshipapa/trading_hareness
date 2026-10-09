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
        self.previous_request_json = edge_api.request_json
        self.previous_ephemeral_note_link = edge_api.ephemeral_note_link
        self.previous_collect_single_note = edge_api.collect_single_note
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
        edge_api.request_json = self.previous_request_json
        edge_api.ephemeral_note_link = self.previous_ephemeral_note_link
        edge_api.collect_single_note = self.previous_collect_single_note
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

    def machine_json(self, path, payload):
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "X-XHS-Collector-Token": edge_api.TOKEN},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
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
        with self.browser.open(self.base + "/xhs/tokens.css", timeout=3) as response:
            tokens = response.read().decode()
            self.assertEqual(response.status, 200)
            self.assertIn("--ink:", tokens)
            self.assertIn("frontend-shared", tokens)

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

    def test_feishu_delivery_adds_fresh_signed_links_without_persisting_tokens(self):
        note_id = "d" * 24
        item = normalize({
            "id": note_id,
            "note_card": {"title": "Signed source", "desc": "正文", "user": {"nickname": "Author"}},
        }, "single:" + note_id)
        edge_api.STORE.enqueue_single_note(item, deliver_to_feishu=True)
        claimed = edge_api.STORE.claim("single-worker")
        edge_api.STORE.complete(claimed["job_id"], claimed["lease_token"], {
            "summary": "本地摘要", "model": "fake",
        })
        job = edge_api.STORE.ready_deliveries()[0]
        signed_url = (
            "https://www.xiaohongshu.com/explore/" + note_id
            + "?xsec_token=fixture-token&xsec_source=pc_share"
        )
        sent = []
        edge_api.ephemeral_note_link = lambda value: signed_url if value == note_id else ""
        edge_api.request_json = lambda url, payload, timeout=30: sent.append(payload)
        edge_api.deliver_one(job)
        delivery = edge_api.STORE.list_delivery_jobs()[0]
        self.assertEqual(delivery["status"], "sent")
        rendered = "\n".join(payload["content"]["text"] for payload in sent)
        self.assertIn("xsec_token=fixture-token", rendered)
        self.assertIn("本地摘要", rendered)
        with edge_api.STORE.connect() as db:
            raw = " ".join(str(row[0]) for row in db.execute("SELECT payload,result FROM jobs"))
        self.assertNotIn("fixture-token", raw)

    def test_dashboard_submits_and_lists_a_single_note_analysis(self):
        def fake_collect(store, _source_root, _cookie_file, value, *, deliver_to_feishu=False):
            self.assertIn('xiaohongshu.com', value)
            item = normalize({
                'id': 'e' * 24,
                'note_card': {'title': 'SGLang serving', 'desc': 'Radix cache',
                              'user': {'nickname': 'Systems Author'}},
            }, 'single:' + 'e' * 24)
            return store.enqueue_single_note(item, deliver_to_feishu=deliver_to_feishu)

        edge_api.collect_single_note = fake_collect
        self.browser.open(self.base + "/xhs/", timeout=3).close()
        status, payload = self.browser_json('/v1/dashboard/single-notes', {
            'url': 'https://www.xiaohongshu.com/explore/' + 'e' * 24,
            'deliver_to_feishu': False,
        })
        self.assertEqual(status, 202)
        self.assertEqual(payload['job_status'], 'pending')
        _, listing = self.browser_json('/v1/single-notes')
        self.assertEqual(listing['jobs'][0]['title'], 'SGLang serving')
        self.assertFalse(listing['jobs'][0]['deliver_to_feishu'])
        self.assertNotIn('payload', listing['jobs'][0])

    def test_note_link_renders_local_preview_when_signed_xhs_link_is_unavailable(self):
        note_id = 'c' * 24
        item = normalize({
            'id': note_id,
            'note_card': {
                'title': 'KV cache systems', 'desc': '正文内容',
                'user': {'nickname': 'Infra Author'},
                'image_list': [{'url_default': 'https://ci.xiaohongshu.com/notes_pre_post/image-1?imageView2/format/jpeg'}],
            },
        }, 'single:' + note_id)
        edge_api.STORE.add_note(item, 'single:' + note_id)
        self.browser.open(self.base + "/xhs/", timeout=3).close()
        with self.browser.open(self.base + '/xhs/open/' + note_id, timeout=3) as response:
            page = response.read().decode()
        self.assertIn('KV cache systems', page)
        self.assertIn('正文内容', page)
        self.assertIn('ci.xiaohongshu.com', page)

    def test_worker_can_extend_a_processing_lease(self):
        edge_api.STORE.enqueue_single_note(normalize({
            'id': 'f' * 24, 'note_card': {'title': 'Long analysis', 'desc': 'systems'},
        }, 'single:' + 'f' * 24))
        job = edge_api.STORE.claim('mac')
        status, payload = self.machine_json('/v1/worker/heartbeat', {
            'job_id': job['job_id'], 'lease_token': job['lease_token'],
        })
        self.assertEqual(status, 200)
        self.assertEqual(payload['status'], 'extended')

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

    def test_dashboard_can_retry_a_failed_recommendation_filter(self):
        note = normalize({
            "id": "b" * 24,
            "note_card": {"title": "Failed filter", "desc": "CUDA", "user": {"nickname": "Infra"}},
        }, "recommendation:failed")
        edge_api.STORE.add_note(note, "recommendation:failed")
        edge_api.STORE.create_recommendation_run("failed-dashboard", requested=1)
        edge_api.STORE.add_recommendation_item("failed-dashboard", note, 1)
        edge_api.STORE.queue_recommendation_filter("failed-dashboard")
        job = edge_api.STORE.claim("classifier", lane="batch")
        with edge_api.STORE.connect() as db:
            db.execute("UPDATE jobs SET attempts=5 WHERE job_id=?", (job["job_id"],))
        edge_api.STORE.fail(job["job_id"], job["lease_token"], "HTTPError:502")

        self.browser.open(self.base + "/xhs/", timeout=3).close()
        status, payload = self.browser_json("/v1/dashboard/recommendations/retry", {
            "run_id": "failed-dashboard",
        })
        self.assertEqual(status, 202)
        self.assertEqual(payload["run_id"], "failed-dashboard")
        self.assertEqual(edge_api.STORE.recommendation_run("failed-dashboard")["status"], "filter_queued")

    def test_topic_collection_routes_accept_dashboard_and_machine_calls(self):
        self.browser.open(self.base + "/xhs/", timeout=3).close()
        status, payload = self.browser_json("/v1/dashboard/topics/run", {"trigger": "dashboard"})
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "accepted")
        status, payload = self.machine_json("/v1/topics/run", {"trigger": "n8n_topics_daily"})
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "accepted")

    def test_topic_upsert_persists_search_keywords_for_the_collection_lane(self):
        self.browser.open(self.base + "/xhs/", timeout=3).close()
        status, payload = self.browser_json("/v1/dashboard/topics", {
            "action": "upsert", "slug": "agents", "name": "智能体",
            "search_keywords": ["Agent 框架", "多智能体"],
            "include_keywords": ["Agent"], "threshold": 0.6,
        })
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "updated")
        _, listing = self.browser_json("/v1/topics")
        topic = next(row for row in listing["topics"] if row["slug"] == "agents")
        self.assertEqual(topic["policy"]["search_keywords"], ["Agent 框架", "多智能体"])
        queries = edge_api.STORE.topic_search_queries()
        self.assertIn({"slug": "agents", "keyword": "多智能体"}, queries)

    def test_digest_routes_queue_list_and_serve_the_rendered_markdown(self):
        value = normalize({"id": "1" * 24, "note_card": {"title": "GPU 实践", "desc": "正文",
                           "user": {"nickname": "Infra"}, "time": 1750000000000}},
                          "topic:ai_infra:GPU")
        edge_api.STORE.add_note(value, "topic:ai_infra:GPU")
        edge_api.STORE.enqueue_pending()
        job = edge_api.STORE.claim("screener")
        edge_api.STORE.complete(job["job_id"], job["lease_token"], {
            "decisions": [{"candidate_id": job["candidate_ids"][0], "decision": "include",
                           "topics": [{"topic_id": "ai_infra", "score": .9}],
                           "relevance_score": .9, "confidence": .9, "reason": "相关"}],
            "model": "fake", "input_sha256": "test"})
        self.browser.open(self.base + "/xhs/", timeout=3).close()
        status, payload = self.browser_json("/v1/dashboard/digest/run", {"trigger": "dashboard"})
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "queued")
        date_value = payload["digest_date"]
        status, duplicate = self.machine_json("/v1/digest/run", {"trigger": "n8n_digest_daily"})
        self.assertEqual(status, 202)
        self.assertEqual(duplicate["status"], "duplicate")
        _, listing = self.browser_json("/v1/digests")
        self.assertEqual(listing["digests"][0]["digest_date"], date_value)
        claimed = edge_api.STORE.claim("digest-worker")
        while claimed and claimed["job_type"] != "daily_digest":
            edge_api.STORE.complete(claimed["job_id"], claimed["lease_token"],
                                    {"summary": "x", "model": "fake"})
            claimed = edge_api.STORE.claim("digest-worker")
        edge_api.STORE.complete(claimed["job_id"], claimed["lease_token"],
                                {"summary": "## 今日导读\n- 要点", "model": "fake"})
        status, detail = self.browser_json(f"/v1/digests/detail?date={date_value}")
        self.assertEqual(status, 200)
        self.assertIn("今日导读", detail["digest"]["summary"])
        self.assertEqual(detail["digest"]["notes"][0]["note_id"], "1" * 24)
        self.assertNotIn("xsec_token", json.dumps(detail))
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.browser_json("/v1/digests/detail?date=not-a-date")
        self.assertEqual(error.exception.code, 400)
        error.exception.close()

    def test_dashboard_recommendation_runs_do_not_reuse_hourly_scheduler_keys(self):
        first = edge_api._recommendation_run_id({"trigger": "dashboard"}, 50)
        second = edge_api._recommendation_run_id({"trigger": "dashboard"}, 50)
        self.assertTrue(first.startswith("xhs-reco-manual-"))
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
