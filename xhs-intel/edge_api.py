#!/usr/bin/env python3
"""Edge HTTP boundary for n8n, the Mac AI worker, and Feishu delivery."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from collector import collect, collect_watched  # noqa: E402
from common import error_code, request_json  # noqa: E402
from operations import (  # noqa: E402
    OperationError,
    SpiderRuntime,
    capability_text,
    command_to_operation,
    format_result,
    parse_operation_payload,
    sanitize,
)
from store import Conflict, Store  # noqa: E402


HOST = os.environ.get("XHS_COLLECTOR_HOST", "127.0.0.1")
PORT = int(os.environ.get("XHS_COLLECTOR_PORT", "18790"))
TOKEN = os.environ.get("XHS_COLLECTOR_TOKEN", "")
SOURCE_ROOT = Path(os.environ.get("XHS_SOURCE_ROOT", "/opt/xhs"))
COOKIE_FILE = Path(os.environ.get("XHS_COOKIE_FILE", "/run/secrets/xhs_cookie"))
STATE_DIR = Path(os.environ.get("XHS_STATE_DIR", "/var/lib/xhs-collector"))
STORE = Store(STATE_DIR / "queue.sqlite3")
DEFAULT_QUERIES = [x.strip() for x in os.environ.get("XHS_KEYWORDS", "AI infra,推理系统,大模型部署,CUDA,算子优化").split(",") if x.strip()]
FETCH_LIMIT = max(1, min(20, int(os.environ.get("XHS_FETCH_LIMIT", "8"))))
WATCH_FETCH_LIMIT = max(1, min(20, int(os.environ.get("XHS_WATCH_FETCH_LIMIT", "5"))))
FEISHU_WEBHOOK = os.environ.get("XHS_FEISHU_WEBHOOK_URL", "").strip()
FEISHU_TOKEN = os.environ.get("XHS_ALERT_WEBHOOK_TOKEN", "")
FEISHU_MAX_CHARS = 3000
DELIVERY_INTERVAL = max(5, int(os.environ.get("XHS_DELIVERY_INTERVAL", "15")))
RUNTIME = SpiderRuntime(SOURCE_ROOT, COOKIE_FILE)
RELEASE = {
    "xhs_git_sha": os.environ.get("XHS_SOURCE_GIT_SHA", ""),
    "xhs_source_tree_sha256": os.environ.get("XHS_SOURCE_TREE_SHA256", ""),
    "xhs_intel_tree_sha256": os.environ.get("XHS_INTEL_TREE_SHA256", ""),
    "xhs_workflow_sha256": os.environ.get("XHS_WORKFLOW_SHA256", ""),
}


def cookie_configured() -> bool:
    try:
        value = COOKIE_FILE.read_text(encoding="utf-8").strip() if COOKIE_FILE.is_file() else ""
        fields = {
            item.split("=", 1)[0].strip()
            for item in value.split(";")
            if "=" in item
        }
        return "a1" in fields and "web_session" in fields
    except OSError:
        return False


def auth(handler):
    return bool(TOKEN) and handler.headers.get("X-XHS-Collector-Token", "") == TOKEN


def reply(handler, status, payload):
    data = json.dumps(payload, ensure_ascii=False).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def body(handler):
    size = int(handler.headers.get("Content-Length", "0"))
    if size <= 0 or size > 2_000_000:
        raise ValueError("invalid_request_size")
    data = json.loads(handler.rfile.read(size))
    if not isinstance(data, dict):
        raise ValueError("request_must_be_object")
    return data


def deliver_one(job):
    result = json.loads(job["result"])
    text = str(result.get("summary", ""))
    chunks = [text[index:index + FEISHU_MAX_CHARS] for index in range(0, len(text), FEISHU_MAX_CHARS)] or [text]
    if not FEISHU_WEBHOOK:
        raise RuntimeError("xhs_feishu_webhook_not_configured")
    for index, chunk in enumerate(chunks, 1):
        if STORE.delivered_part(job["job_id"], index):
            continue
        marker = f"\n\n（XHS:{job['job_id']}:{index}/{len(chunks)}）"
        payload = {"msg_type": "text", "content": {"text": chunk + marker}}
        # Bot webhooks do not expose a UUID/idempotency field. The edge ledger
        # records each part before considering the job delivered and includes a
        # bounded marker for operator-side duplicate diagnosis after a crash.
        request_json(FEISHU_WEBHOOK, payload, timeout=30)
        STORE.record_delivery(job["job_id"], index, "feishu-bot-webhook")
    STORE.delivered(job["job_id"])


def delivery_loop():
    while True:
        try:
            for job in STORE.ready_deliveries():
                try:
                    deliver_one(job)
                except Exception as exc:  # noqa: BLE001
                    STORE.delivery_error(job["job_id"], error_code(exc))
        finally:
            time.sleep(DELIVERY_INTERVAL)


def _command_text(payload):
    command = str(payload.get("command") or payload.get("text") or "").strip()
    # The adapter intentionally forwards the text after #xhs.  Keep accepting
    # the full form for direct n8n/API callers and for old queued messages.
    return command if command.lower().startswith("#xhs") else "#xhs " + command


def _watch_user_id(value):
    """Accept an XHS user id or a profile URL, but persist only the id."""
    value = str(value or "").strip()
    if "://" in value:
        parsed = urlparse(value)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2 or parts[-2].lower() != "profile":
            raise OperationError("用户主页 URL 必须是 /user/profile/<用户ID>")
        value = parts[-1]
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,128}", value):
        raise OperationError("用户 ID 或用户主页 URL 无效")
    return value


def command_result(payload):
    message_id = str(payload.get("message_id") or hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24])
    command = _command_text(payload)
    # Keep only a digest in the durable command ledger. Generic API arguments
    # may contain user supplied fields, and the ledger must never become a
    # second secret store.
    command_ledger_key = "sha256:" + hashlib.sha256(command.encode()).hexdigest()
    if not STORE.command(message_id, command_ledger_key):
        return {"status": "duplicate", "message_id": message_id}
    lowered = command.lower()
    try:
        if lowered in {"#xhs status", "#xhs 状态"}:
            status = STORE.status()
            text = f"小红书流水线状态\n库存：{status['notes']} 条笔记、{status['revisions']} 个内容版本\n关注用户：{status['watch_users']} 个\n任务：{status['jobs']}\n最近摘要：{STORE.latest_summary()[:1200]}"
        elif lowered in {"#xhs latest", "#xhs 最新", "#xhs 摘要"}:
            text = STORE.latest_summary()[:2800]
        elif lowered in {"#xhs", "#xhs help", "#xhs 帮助"}:
            text = capability_text()
        elif lowered in {"#xhs watch list", "#xhs watch list users", "#xhs 关注列表"}:
            rows = STORE.list_watch_users()
            text = "关注用户：\n" + ("\n".join(
                f"- {row['user_id']}" + (f"（{row['label']}）" if row['label'] else "")
                for row in rows) if rows else "（空）")
        elif lowered.startswith("#xhs watch add ") or lowered.startswith("#xhs 关注 添加 "):
            tail = command.split(None, 3)[3]
            values = tail.strip().split(None, 1)
            user_id = values[0] if values else ""
            label = values[1].strip() if len(values) > 1 else ""
            user_id = _watch_user_id(user_id)
            label = label[:80]
            STORE.add_watch_user(user_id, label)
            text = f"已加入关注：{user_id}" + (f"（{label}）" if label else "")
        elif lowered.startswith("#xhs watch remove ") or lowered.startswith("#xhs 关注 删除 "):
            user_id = _watch_user_id(command.split(None, 3)[3].strip())
            if STORE.remove_watch_user(user_id):
                text = f"已取消关注：{user_id}"
            else:
                text = f"未找到启用中的关注用户：{user_id}"
        else:
            operation = command_to_operation(command)
            if operation is None:
                text = "无法识别指令。\n\n" + capability_text()
            else:
                namespace, method, args, kwargs = operation
                result = RUNTIME.execute(namespace, method, args, kwargs)
                text = format_result(f"{namespace}.{method}", result)
    except Exception as exc:  # noqa: BLE001
        # User-facing operation errors are intentionally terse; upstream
        # exceptions can contain signed URLs or private request material.
        text = f"小红书操作失败：{error_code(exc)}"
    STORE.command_result(message_id, text)
    return {"status": "accepted", "message_id": message_id}


class Handler(BaseHTTPRequestHandler):
    server_version = "xhs-edge/1"

    def log_message(self, fmt, *args):
        sys.stderr.write("xhs-edge: " + (fmt % args) + "\n")

    def do_GET(self):  # noqa: N802
        if self.path in {"/health", "/v1/status"}:
            if self.path == "/v1/status" and not auth(self):
                reply(self, 401, {"status": "unauthorized"})
                return
            value = STORE.status()
            value.update({"status": "ok", "collector": "Spider_XHS", "cookie_configured": cookie_configured(), "feishu_webhook_configured": bool(FEISHU_WEBHOOK), "release": RELEASE})
            reply(self, 200, value)
            return
        reply(self, 404, {"status": "not_found"})

    def do_POST(self):  # noqa: N802
        if not auth(self):
            reply(self, 401, {"status": "unauthorized"})
            return
        try:
            payload = body(self)
            if self.path == "/v1/run":
                queries = payload.get("keywords") or DEFAULT_QUERIES
                if not isinstance(queries, list):
                    raise ValueError("keywords_must_be_list")
                result = collect(STORE, SOURCE_ROOT, COOKIE_FILE, [str(x) for x in queries[:20]], limit=FETCH_LIMIT, request_key=str(payload.get("run_key") or ""), delay=max(1, int(os.environ.get("XHS_REQUEST_DELAY", "3"))))
                while STORE.enqueue_pending():
                    pass
                reply(self, 200, {**result, "queue": STORE.status().get("jobs", {})})
                return
            if self.path == "/v1/watch/run":
                users = STORE.list_watch_users()
                if not users:
                    reply(self, 200, {"status": "idle", "reason": "no_watch_users", "users": 0})
                    return
                interval = max(5, int(os.environ.get("XHS_WATCH_INTERVAL_SECONDS", "1800")))
                bucket = int(time.time() // interval)
                run_key = str(payload.get("run_key") or f"watch-{bucket}")
                result = collect_watched(
                    STORE, SOURCE_ROOT, COOKIE_FILE, users, limit=WATCH_FETCH_LIMIT,
                    request_key=run_key,
                    delay=max(1, int(os.environ.get("XHS_REQUEST_DELAY", "3"))))
                while STORE.enqueue_pending():
                    pass
                reply(self, 200, {**result, "queue": STORE.status().get("jobs", {})})
                return
            if self.path == "/v1/command":
                reply(self, 200, command_result(payload))
                return
            if self.path == "/v1/operation":
                namespace, method, args, kwargs = parse_operation_payload(payload)
                result = RUNTIME.execute(namespace, method, args, kwargs)
                reply(self, 200, {"status": "completed", "operation": f"{namespace}.{method}", "result": sanitize(result)})
                return
            if self.path == "/v1/worker/claim":
                reply(self, 200, {"job": STORE.claim(str(payload.get("worker") or "mac-ai"))})
                return
            if self.path == "/v1/worker/complete":
                result = {"summary": payload.get("summary"), "model": payload.get("model"), "provider": payload.get("provider"), "input_sha256": payload.get("input_sha256")}
                reply(self, 200, {"status": STORE.complete(str(payload.get("job_id")), str(payload.get("lease_token")), result)})
                return
            if self.path == "/v1/worker/fail":
                STORE.fail(str(payload.get("job_id")), str(payload.get("lease_token")), str(payload.get("error") or "worker_failed"))
                reply(self, 200, {"status": "recorded"})
                return
            reply(self, 404, {"status": "not_found"})
        except Conflict as exc:
            reply(self, 409, {"status": "conflict", "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            reply(self, 502, {"status": "failed", "error": error_code(exc)})


def main():
    threading.Thread(target=delivery_loop, name="xhs-delivery", daemon=True).start()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
