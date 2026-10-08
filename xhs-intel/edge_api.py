#!/usr/bin/env python3
"""Edge HTTP boundary for n8n, the Mac AI worker, and Feishu delivery."""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import secrets
import os
import re
import sys
import threading
import time
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from collector import (  # noqa: E402
    InvalidNoteLink,
    NoteFetchUnavailable,
    collect,
    collect_recommendations,
    collect_single_note,
    collect_watched,
    ephemeral_note_link,
)
from common import error_code, request_json  # noqa: E402
from config import policy_snapshot  # noqa: E402
from operations import (  # noqa: E402
    MUTATING,
    PUBLIC_METHODS,
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
DASHBOARD_ROOT = ROOT / "dashboard"
DASHBOARD_SESSION = secrets.token_urlsafe(32)
STORE = Store(STATE_DIR / "queue.sqlite3")
DEFAULT_QUERIES = [x.strip() for x in os.environ.get("XHS_KEYWORDS", "AI infra,推理系统,大模型部署,CUDA,算子优化").split(",") if x.strip()]
FETCH_LIMIT = max(1, min(20, int(os.environ.get("XHS_FETCH_LIMIT", "8"))))
WATCH_FETCH_LIMIT = max(1, min(20, int(os.environ.get("XHS_WATCH_FETCH_LIMIT", "5"))))
RECOMMEND_FETCH_LIMIT = max(1, min(50, int(os.environ.get("XHS_RECOMMEND_FETCH_LIMIT", "50"))))
RECOMMEND_CATEGORY = os.environ.get("XHS_RECOMMEND_CATEGORY", "homefeed_recommend").strip() or "homefeed_recommend"
RECOMMEND_DELAY = max(1, int(os.environ.get("XHS_RECOMMEND_DELAY", os.environ.get("XHS_REQUEST_DELAY", "3"))))
ADMIN_OPEN_IDS = {value.strip() for value in os.environ.get("XHS_ADMIN_OPEN_IDS", "").split(",") if value.strip()}
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


def dashboard_auth(handler):
    if auth(handler):
        return True
    cookie = SimpleCookie()
    try:
        cookie.load(handler.headers.get("Cookie", ""))
        value = cookie.get("xhs_ui_session")
        return bool(value) and hmac.compare_digest(value.value, DASHBOARD_SESSION)
    except (CookieError, KeyError, TypeError):
        return False


def send(handler, status, data, content_type, headers=None):
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("X-Content-Type-Options", "nosniff")
    for name, value in (headers or {}).items():
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(data)


def reply(handler, status, payload, headers=None):
    data = json.dumps(payload, ensure_ascii=False).encode()
    send(handler, status, data, "application/json; charset=utf-8", headers=headers)


def serve_dashboard(handler, path):
    assets = {
        "/xhs": ("index.html", "text/html; charset=utf-8"),
        "/xhs/": ("index.html", "text/html; charset=utf-8"),
        "/xhs/app.js": ("app.js", "text/javascript; charset=utf-8"),
        "/xhs/style.css": ("style.css", "text/css; charset=utf-8"),
    }
    asset = assets.get(path)
    if not asset:
        return False
    filename, content_type = asset
    target = DASHBOARD_ROOT / filename
    if not target.is_file():
        reply(handler, 503, {"status": "dashboard_unavailable"})
        return True
    headers = {
        "Cache-Control": "no-store" if filename == "index.html" else "no-cache",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        "Referrer-Policy": "no-referrer",
    }
    if filename == "index.html":
        headers["Set-Cookie"] = (
            f"xhs_ui_session={DASHBOARD_SESSION}; Path=/; HttpOnly; SameSite=Strict"
        )
    send(handler, 200, target.read_bytes(), content_type, headers=headers)
    return True


def _note_id_from_path(path, prefix):
    match = re.fullmatch(prefix + r'([0-9a-fA-F]{24})', path)
    return match.group(1).lower() if match else ''


def _note_preview_html(note, signed_url=''):
    title = html.escape(str(note.get('title') or '小红书笔记'))
    author = html.escape(str(note.get('author') or '未知作者'))
    published = html.escape(str(note.get('published_at') or ''))
    body = html.escape(str(note.get('text') or '暂无正文')).replace('\n', '<br>')
    images = []
    for value in (note.get('image_urls') or [])[:18]:
        parsed = urlparse(str(value))
        if parsed.scheme == 'https' and parsed.hostname == 'ci.xiaohongshu.com':
            images.append(
                f'<figure><img loading="lazy" src="{html.escape(value, quote=True)}" alt="笔记图片"></figure>'
            )
    original = ''
    if signed_url:
        original = (
            f'<p><a href="{html.escape(signed_url, quote=True)}" target="_blank" '
            'rel="noreferrer">在小红书打开原文（签名链接短时有效）</a></p>'
        )
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><meta name="referrer" content="no-referrer">
<style>body{{margin:0;background:#f5f5f2;color:#1f2428;font:16px/1.75 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:860px;margin:0 auto;padding:32px 20px 56px;background:#fff;min-height:100vh}}
h1{{font-size:28px;line-height:1.3;margin:0 0 8px}}.meta{{color:#687078;font-size:14px;margin-bottom:24px}}
.text{{white-space:normal;margin:0 0 24px}}figure{{margin:16px 0}}img{{display:block;max-width:100%;height:auto;border-radius:6px;background:#eee}}
a{{color:#b5452d}}</style></head><body><main><h1>{title}</h1>
<div class="meta">{author}{' · ' + published if published else ''}</div><div class="text">{body}</div>
{''.join(images)}{original}</main></body></html>'''


def serve_note_link_or_preview(handler, path):
    """Open a fresh signed XHS page or render the durable local note preview."""
    note_id = _note_id_from_path(path, r'/xhs/open/')
    preview_id = _note_id_from_path(path, r'/xhs/note/')
    if not note_id and not preview_id:
        return False
    note_id = note_id or preview_id
    note = STORE.latest_note(note_id)
    if not note:
        reply(handler, 404, {'status': 'not_found', 'error': 'note_not_found'})
        return True
    if path.startswith('/xhs/open/'):
        signed_url = ephemeral_note_link(note_id)
        if signed_url:
            handler.send_response(302)
            handler.send_header('Location', signed_url)
            handler.send_header('Cache-Control', 'no-store')
            handler.send_header('Referrer-Policy', 'no-referrer')
            handler.end_headers()
            return True
    data = _note_preview_html(note, ephemeral_note_link(note_id)).encode('utf-8')
    send(handler, 200, data, 'text/html; charset=utf-8', headers={
        'Cache-Control': 'no-store',
        'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; img-src https://ci.xiaohongshu.com; frame-ancestors 'none'; base-uri 'none'",
        'Referrer-Policy': 'no-referrer',
    })
    return True


def body(handler):
    size = int(handler.headers.get("Content-Length", "0"))
    if size <= 0 or size > 2_000_000:
        raise ValueError("invalid_request_size")
    data = json.loads(handler.rfile.read(size))
    if not isinstance(data, dict):
        raise ValueError("request_must_be_object")
    return data


def _signed_delivery_links(job):
    """Return fresh signed links without reading or persisting token material."""
    try:
        payload = json.loads(str(job.get("payload") or "{}"))
    except (TypeError, ValueError):
        return []
    notes = payload.get("notes") if isinstance(payload, dict) else None
    if not isinstance(notes, list):
        return []
    links = []
    seen = set()
    for note in notes:
        if not isinstance(note, dict):
            continue
        note_id = str(note.get("note_id") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{24}", note_id):
            continue
        signed_url = ephemeral_note_link(note_id)
        if signed_url and signed_url not in seen:
            seen.add(signed_url)
            links.append(signed_url)
    return links


def _delivery_chunks(text, signed_links):
    """Chunk summaries and signed URLs without splitting a URL across messages."""
    chunks = [text[index:index + FEISHU_MAX_CHARS]
              for index in range(0, len(text), FEISHU_MAX_CHARS)] or [text]
    if not signed_links:
        return chunks
    current = "原文直链（签名链接短时有效）"
    for signed_url in signed_links:
        line = f"原文直链：{signed_url}"
        candidate = current + "\n" + line
        if len(candidate) > FEISHU_MAX_CHARS and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def deliver_one(job):
    result = json.loads(job["result"])
    text = str(result.get("summary", ""))
    chunks = _delivery_chunks(text, _signed_delivery_links(job))
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


def _admin_actor(payload):
    actor = str(payload.get('sender_open_id') or payload.get('actor_open_id') or payload.get('from_open_id') or '').strip()
    if not actor or not ADMIN_OPEN_IDS or actor not in ADMIN_OPEN_IDS:
        raise OperationError('管理员身份未配置或无权执行此审核操作')
    return actor


def _recommendation_run_id(payload, requested):
    payload = dict(payload or {})
    explicit = str(payload.get('run_id') or payload.get('run_key') or '').strip()
    if explicit:
        return explicit
    # Dashboard actions are user initiated. Give each click its own durable run
    # so a failed AI filter cannot be hidden behind the hourly scheduler key.
    if str(payload.get('trigger') or '').strip() == 'dashboard':
        return f"xhs-reco-manual-{int(time.time() * 1000)}-{secrets.token_hex(4)}"
    return 'xhs-reco-' + hashlib.sha256(f"{int(time.time() // 3600)}:{requested}".encode()).hexdigest()[:24]


def run_recommendation(payload=None):
    payload = dict(payload or {})
    requested = max(1, min(RECOMMEND_FETCH_LIMIT, int(payload.get('limit') or RECOMMEND_FETCH_LIMIT)))
    run_id = _recommendation_run_id(payload, requested)
    existing = STORE.recommendation_run(run_id)
    if existing and existing.get('status') in {'summary_queued', 'filtered', 'completed', 'filter_queued', 'collected', 'running'}:
        return {'status': 'duplicate', 'run_id': run_id, 'run': existing,
                'queue': STORE.status().get('jobs', {})}
    STORE.create_recommendation_run(run_id, category=RECOMMEND_CATEGORY, requested=requested,
                                    policy=policy_snapshot())
    result = collect_recommendations(STORE, SOURCE_ROOT, COOKIE_FILE, run_id=run_id,
                                      limit=requested, category=RECOMMEND_CATEGORY,
                                      delay=RECOMMEND_DELAY)
    if result.get('status') in {'completed', 'partial'} and result.get('fetched', 0):
        try:
            result['filter_job_id'] = STORE.queue_recommendation_filter(run_id)
        except Exception as exc:  # noqa: BLE001
            result['filter_error'] = error_code(exc)
    result['run'] = STORE.recommendation_run(run_id)
    result['queue'] = STORE.status().get('jobs', {})
    return result


def _start_recommendation_async(payload):
    payload = dict(payload or {})
    requested = max(1, min(RECOMMEND_FETCH_LIMIT, int(payload.get('limit') or RECOMMEND_FETCH_LIMIT)))
    run_hint = _recommendation_run_id(payload, requested)
    payload['run_id'] = run_hint
    thread = threading.Thread(target=run_recommendation, args=(payload,), name='xhs-recommendation', daemon=True)
    thread.start()
    return {'status': 'accepted', 'run_id': run_hint, 'message': '推荐扫描已在 Edge 后台启动，请稍后查询 status'}


def sync_following(payload=None):
    """Read and normalize the current XHS following roster.

    This uses the existing read-only Spider_XHS live method as a first source;
    the canonical PC intimacy endpoint will be added as a separate provider
    once it is pinned in Spider_XHS.  We record the endpoint so the two
    rosters can later be reconciled instead of silently merged.
    """
    payload = dict(payload or {})
    max_pages = max(1, min(10, int(payload.get('max_pages') or 5)))
    page_size = max(1, min(200, int(payload.get('page_size') or 200)))
    max_accounts = max(1, min(1000, int(payload.get('max_accounts') or 1000)))
    accounts = []
    endpoint = 'live:/api/im/web/users/following/all'
    try:
        for page in range(1, max_pages + 1):
            response = RUNTIME.execute('live', 'get_following', [], {'page': page, 'size': page_size})
            data = response.get('data') if isinstance(response, dict) else response
            if isinstance(data, dict):
                items = (data.get('users') or data.get('items') or data.get('list')
                         or data.get('follow_user_d_t_o_list') or [])
            else:
                items = data if isinstance(data, list) else []
            if not items:
                break
            for item in items:
                if not isinstance(item, dict):
                    continue
                # `rid` is the stable PC intimacy id when this provider is
                # swapped in; live responses generally use user_id/id.
                user_id = str(item.get('rid') or item.get('user_id') or item.get('userId') or item.get('id') or '').strip()
                if not user_id or any(row['user_id'] == user_id for row in accounts):
                    continue
                accounts.append({'user_id': user_id,
                                 'nickname': str(item.get('nickname') or item.get('nick_name') or item.get('name') or ''),
                                 'source_endpoint': endpoint,
                                 # Keep only stable profile text; image/CDN
                                 # URLs and any upstream signed fields stay out
                                 # of the durable following snapshot.
                                 'raw': {'nickname': str(item.get('nickname') or item.get('nick_name') or item.get('name') or ''),
                                         'description': str(item.get('description') or item.get('desc') or '')[:1000]}})
                if len(accounts) >= max_accounts:
                    break
            if len(items) < page_size or len(accounts) >= max_accounts:
                break
        saved = STORE.upsert_following_accounts(accounts, endpoint)
        return {'status': 'completed' if accounts else 'empty', 'source_endpoint': endpoint, 'pages': page,
                'fetched': len(accounts), 'saved': saved, 'max_accounts': max_accounts,
                **({'reason': 'no_following_accounts'} if not accounts else {})}
    except Exception as exc:
        return {'status': 'failed', 'source_endpoint': endpoint, 'fetched': len(accounts),
                'saved': STORE.upsert_following_accounts(accounts, endpoint) if accounts else 0,
                'error': error_code(exc)}


def _start_following_sync_async(payload=None):
    thread = threading.Thread(target=sync_following, args=(payload or {},),
                              name='xhs-following-sync', daemon=True)
    thread.start()
    return {'status': 'accepted', 'message': '关注账号快照已在 Edge 后台同步，请稍后查询 following list'}


def screen_following(payload=None):
    """Build a bounded profile-classification job from recent user posts."""
    payload = dict(payload or {})
    limit = max(1, min(50, int(payload.get('limit') or 50)))
    delay = max(0, int(payload.get('delay') or 1))
    accounts = STORE.list_following_accounts(state='candidate', limit=limit)
    profiles = []
    for account in accounts:
        profile = {}
        try:
            profile = json.loads(account.get('profile_json') or '{}')
        except (TypeError, ValueError):
            profile = {}
        recent = []
        try:
            result = RUNTIME.execute('pc', 'get_user_note_info',
                                    [account['user_id'], '', '', 'pc_user'], {})
            ok, _message, body_value = result if isinstance(result, tuple) and len(result) == 3 else (True, '', result)
            if ok:
                data = (body_value or {}).get('data') or {}
                items = data.get('notes') or data.get('items') or []
                items = sorted((item for item in items if isinstance(item, dict)),
                               key=lambda item: float(item.get('time') or 0), reverse=True)
                for item in items[:3]:
                    recent.append({'note_id': str(item.get('id') or item.get('note_id') or ''),
                                   'title': str(item.get('title') or item.get('display_title') or ''),
                                   'text': str(item.get('desc') or '')[:1200],
                                   'time': item.get('time')})
        except Exception:
            # A stale or non-PC user id should still be reviewable by nickname;
            # the failure is represented by an empty recent_notes list.
            recent = []
        profiles.append({'user_id': account['user_id'], 'nickname': account['nickname'],
                         'description': str(profile.get('description') or profile.get('desc') or ''),
                         'recent_notes': recent,
                         'recent_note_ids': [row['note_id'] for row in recent if row['note_id']]})
        if delay:
            time.sleep(delay)
    if not profiles:
        return {'status': 'idle', 'reason': 'no_following_candidates', 'count': 0}
    return STORE.queue_following_filter(profiles)


def _start_following_screen_async(payload=None):
    thread = threading.Thread(target=screen_following, args=(payload or {},),
                              name='xhs-following-screen', daemon=True)
    thread.start()
    return {'status': 'accepted', 'message': '关注账号筛选已在 Edge 后台启动，请稍后查询 candidates'}


def run_watch(payload=None):
    payload = dict(payload or {})
    users = STORE.list_watch_users()
    if not users:
        return {"status": "idle", "reason": "no_watch_users", "users": 0}
    interval = max(5, int(os.environ.get("XHS_WATCH_INTERVAL_SECONDS", "1800")))
    bucket = int(time.time() // interval)
    run_key = str(payload.get("run_key") or f"watch-{bucket}")
    result = collect_watched(
        STORE, SOURCE_ROOT, COOKIE_FILE, users, limit=WATCH_FETCH_LIMIT,
        request_key=run_key,
        delay=max(1, int(os.environ.get("XHS_REQUEST_DELAY", "3"))))
    while STORE.enqueue_pending():
        pass
    return {**result, "queue": STORE.status().get("jobs", {})}


def _start_watch_async(payload=None):
    thread = threading.Thread(target=run_watch, args=(payload or {},),
                              name="xhs-watch", daemon=True)
    thread.start()
    return {"status": "accepted", "message": "监控名单扫描已在 Edge 后台启动"}


def _query_int(query, name, default, minimum, maximum):
    try:
        value = int(query.get(name, [default])[0])
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _recommendation_item_for_dashboard(row):
    try:
        note = json.loads(row.get("body") or "{}")
    except (TypeError, ValueError):
        note = {}
    try:
        topics = json.loads(row.get("topics_json") or "[]")
    except (TypeError, ValueError):
        topics = []
    return {
        "candidate_id": row.get("candidate_id"),
        "note_id": row.get("note_id"),
        "source_rank": row.get("source_rank"),
        "state": row.get("state"),
        "decision": row.get("decision"),
        "relevance_score": row.get("relevance_score"),
        "confidence": row.get("confidence"),
        "reason": row.get("reason") or "",
        "topics": topics if isinstance(topics, list) else [],
        "title": str(note.get("title") or "")[:240],
        "text": str(note.get("text") or "")[:1000],
        "author": str(note.get("author") or "")[:120],
        "published_at": note.get("published_at"),
        "url": f"https://www.xiaohongshu.com/explore/{row.get('note_id')}",
        "preview_url": f"/xhs/open/{row.get('note_id')}",
    }


def _bounded_operation_result(value):
    safe = sanitize(value)
    encoded = json.dumps(safe, ensure_ascii=False, default=str)
    if len(encoded) <= 200_000:
        return safe
    return {"truncated": True, "preview": encoded[:200_000]}


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
        elif lowered in {"#xhs intel topic list", "#xhs intel topics", "#xhs 主题列表"}:
            rows = STORE.list_topics(enabled=None)
            text = "Topic 配置：\n" + ("\n".join(
                f"- {row['slug']}：{row['name']}（{'启用' if row['enabled'] else '停用'}，v{row['active_version']}）"
                for row in rows) if rows else "（空）")
        elif lowered in {"#xhs intel recommendation latest", "#xhs intel reco latest", "#xhs 推荐状态"}:
            status = STORE.status()
            rows = status.get('recommendation_runs') or []
            text = "最近推荐扫描：\n" + ("\n".join(
                f"- {row['run_id']}：{row['status']}，抓取 {row['fetched']}，入选 {row['selected']}，待审 {row['review']}，排除 {row['rejected']}"
                for row in rows[:5]) if rows else "（暂无）")
        elif lowered in {"#xhs intel following list", "#xhs intel following", "#xhs 关注候选"}:
            rows = STORE.list_following_accounts(limit=50)
            text = "关注账号快照（内部候选）：\n" + ("\n".join(
                f"- {row['user_id']} {row['nickname']}（{row['state']}）" for row in rows) if rows else "（暂无，请先执行 following sync）")
        elif lowered in {"#xhs intel following sync", "#xhs 关注同步"}:
            _start_following_sync_async({'trigger': 'feishu_command'})
            text = "已启动关注账号只读同步，完成后可使用 #xhs intel following list 查看。"
        elif lowered in {"#xhs intel following screen", "#xhs 关注筛选"}:
            _start_following_screen_async({'trigger': 'feishu_command'})
            text = "已启动关注账号筛选，请稍后使用 #xhs intel following candidates 查看结果。"
        elif lowered in {"#xhs intel following candidates", "#xhs 关注候选列表"}:
            rows = STORE.list_profile_candidates(limit=50)
            text = "关注候选筛选结果：\n" + ("\n".join(
                f"- {row['user_id']} {row.get('nickname') or ''}：{row['decision']}，{row['reason']}"
                for row in rows) if rows else "（暂无，请先执行 following screen）")
        elif lowered.startswith("#xhs intel following approve "):
            actor = _admin_actor(payload)
            user_id = _watch_user_id(command.split()[-1])
            result = STORE.apply_following_candidate(user_id, actor)
            text = f"已加入内部监控列表：{result['user_id']}（审核人已记录）"
        elif lowered.startswith("#xhs intel following reject "):
            actor = _admin_actor(payload)
            user_id = _watch_user_id(command.split()[-1])
            STORE.reject_following_candidate(user_id, actor)
            text = f"已拒绝关注候选：{user_id}"
        elif lowered.startswith("#xhs intel scan recommendations") or lowered.startswith("#xhs intel scan reco"):
            parts = command.split()
            requested = int(parts[-1]) if parts and parts[-1].isdigit() else RECOMMEND_FETCH_LIMIT
            accepted = _start_recommendation_async({'limit': requested, 'trigger': 'feishu_command'})
            text = f"已启动推荐扫描（最多 {min(requested, RECOMMEND_FETCH_LIMIT)} 条）。请稍后使用 #xhs intel recommendation latest 查询。"
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
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if serve_dashboard(self, path):
            return
        if path != "/health" and not dashboard_auth(self):
            reply(self, 401, {"status": "unauthorized"})
            return
        if serve_note_link_or_preview(self, path):
            return
        if path in {"/health", "/v1/status"}:
            value = STORE.status()
            value.update({"status": "ok", "collector": "Spider_XHS", "cookie_configured": cookie_configured(), "feishu_webhook_configured": bool(FEISHU_WEBHOOK), "release": RELEASE})
            reply(self, 200, value)
            return
        if path == "/v1/dashboard":
            value = STORE.status()
            value.update({
                "status": "ok",
                "collector": "Spider_XHS",
                "cookie_configured": cookie_configured(),
                "feishu_webhook_configured": bool(FEISHU_WEBHOOK),
                "release": RELEASE,
                "following": {
                    "total": STORE.count_following_accounts(),
                    "candidate": STORE.count_following_accounts("candidate"),
                    "active": STORE.count_following_accounts("active"),
                    "rejected": STORE.count_following_accounts("rejected"),
                },
                "topics": STORE.list_topics(enabled=None),
            })
            reply(self, 200, value)
            return
        if path == "/v1/recommendations/status":
            value = STORE.status()
            reply(self, 200, {"status": "ok", "runs": value.get("recommendation_runs", []),
                              "counts": value.get("recommendation_counts", []), "jobs": value.get("jobs", {})})
            return
        if path == "/v1/recommendations/items":
            run_id = str(query.get("run_id", [""])[0]).strip()
            if not run_id:
                reply(self, 400, {"status": "failed", "error": "run_id_required"})
                return
            states = [value for value in query.get("state", []) if value]
            rows = STORE.list_recommendation_items(run_id, states=states or None)
            reply(self, 200, {"status": "ok", "run_id": run_id,
                              "items": [_recommendation_item_for_dashboard(row) for row in rows]})
            return
        if path == "/v1/topics":
            reply(self, 200, {"status": "ok", "topics": STORE.list_topics(enabled=None)})
            return
        if path == "/v1/watch-users":
            enabled = str(query.get("enabled", ["active"])[0]).strip().lower()
            filter_value = None if enabled == "all" else enabled not in {"0", "false", "disabled"}
            reply(self, 200, {"status": "ok", "users": STORE.list_watch_users(enabled=filter_value)})
            return
        if path == "/v1/following/candidates":
            limit = _query_int(query, "limit", 100, 1, 500)
            reply(self, 200, {"status": "ok", "candidates": STORE.list_profile_candidates(limit=limit)})
            return
        if path == "/v1/jobs":
            limit = _query_int(query, "limit", 50, 1, 200)
            reply(self, 200, {"status": "ok", "jobs": STORE.list_jobs(limit=limit)})
            return
        if path == "/v1/single-notes":
            limit = _query_int(query, "limit", 20, 1, 100)
            reply(self, 200, {"status": "ok", "jobs": STORE.list_single_note_jobs(limit=limit)})
            return
        if path == "/v1/feishu/status":
            limit = _query_int(query, "limit", 50, 1, 200)
            reply(self, 200, {"status": "ok", "configured": bool(FEISHU_WEBHOOK),
                              "deliveries": STORE.list_delivery_jobs(limit=limit)})
            return
        if path == "/v1/capabilities":
            mutating = {f"{namespace}.{method}" for namespace, method in MUTATING}
            reply(self, 200, {
                "status": "ok",
                "namespaces": [
                    {"name": namespace, "methods": [
                        {"name": method, "operation": f"{namespace}.{method}",
                         "mutating": f"{namespace}.{method}" in mutating}
                        for method in methods
                    ]}
                    for namespace, methods in PUBLIC_METHODS.items()
                ],
            })
            return
        if path == "/v1/following":
            limit = _query_int(query, "limit", 100, 1, 1000)
            offset = _query_int(query, "offset", 0, 0, 1_000_000)
            state = str(query.get("state", [""])[0]).strip() or None
            reply(self, 200, {
                "status": "ok",
                "state": state,
                "offset": offset,
                "limit": limit,
                "total": STORE.count_following_accounts(state),
                "accounts": STORE.list_following_accounts(state=state, limit=limit, offset=offset),
            })
            return
        reply(self, 404, {"status": "not_found"})

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        dashboard_path = path.startswith("/v1/dashboard/")
        if (dashboard_path and not dashboard_auth(self)) or (not dashboard_path and not auth(self)):
            reply(self, 401, {"status": "unauthorized"})
            return
        try:
            payload = body(self)
            if path == "/v1/dashboard/recommendations/run":
                reply(self, 202, _start_recommendation_async(payload))
                return
            if path == "/v1/dashboard/recommendations/retry":
                run_id = str(payload.get("run_id") or "").strip()
                if not run_id:
                    raise ValueError("recommendation_run_id_required")
                if not STORE.retry_recommendation_filter(run_id):
                    reply(self, 409, {"status": "not_retryable", "run_id": run_id})
                    return
                reply(self, 202, {"status": "accepted", "run_id": run_id,
                                  "message": "AI 筛选已重新进入队列"})
                return
            if path == "/v1/dashboard/following/sync":
                reply(self, 202, _start_following_sync_async(payload))
                return
            if path == "/v1/dashboard/following/screen":
                reply(self, 202, _start_following_screen_async(payload))
                return
            if path == "/v1/dashboard/watch/run":
                reply(self, 202, _start_watch_async(payload))
                return
            if path == "/v1/dashboard/watch-users":
                action = str(payload.get("action") or "add").strip().lower()
                user_id = _watch_user_id(payload.get("user_id") or payload.get("url"))
                if action == "remove":
                    changed = STORE.remove_watch_user(user_id)
                    reply(self, 200, {"status": "updated" if changed else "not_found", "user_id": user_id})
                elif action == "add":
                    STORE.add_watch_user(user_id, str(payload.get("label") or "")[:80],
                                         source="dashboard", topic_ids=payload.get("topic_ids") or [])
                    reply(self, 200, {"status": "updated", "user_id": user_id})
                else:
                    raise ValueError("invalid_watch_user_action")
                return
            if path == "/v1/dashboard/topics":
                action = str(payload.get("action") or "upsert").strip().lower()
                if action in {"disable", "enable"}:
                    changed = STORE.set_topic_enabled(payload.get("slug"), action == "enable")
                    reply(self, 200, {"status": "updated" if changed else "not_found",
                                      "slug": payload.get("slug")})
                elif action == "upsert":
                    reply(self, 200, {"status": "updated", "topic": STORE.upsert_topic(payload)})
                else:
                    raise ValueError("invalid_topic_action")
                return
            if path == "/v1/dashboard/following/candidates":
                action = str(payload.get("action") or "").strip().lower()
                user_id = _watch_user_id(payload.get("user_id"))
                if action == "approve":
                    result = STORE.apply_following_candidate(user_id, "xhs-dashboard")
                elif action == "reject":
                    result = STORE.reject_following_candidate(user_id, "xhs-dashboard")
                else:
                    raise ValueError("invalid_candidate_action")
                reply(self, 200, {"status": "updated", "result": result})
                return
            if path == "/v1/dashboard/feishu/messages":
                if not FEISHU_WEBHOOK:
                    raise OperationError("XHS Feishu webhook is not configured")
                result = STORE.enqueue_manual_message(payload.get("text"), payload.get("request_id"))
                reply(self, 202, {"status": "accepted", **result})
                return
            if path == "/v1/dashboard/single-notes":
                deliver_to_feishu = bool(payload.get("deliver_to_feishu"))
                if deliver_to_feishu and not FEISHU_WEBHOOK:
                    raise OperationError("XHS Feishu webhook is not configured")
                try:
                    result = collect_single_note(
                        STORE, SOURCE_ROOT, COOKIE_FILE,
                        payload.get("url") or payload.get("text"),
                        deliver_to_feishu=deliver_to_feishu,
                    )
                except InvalidNoteLink as exc:
                    reply(self, 400, {"status": "failed", "error": str(exc)})
                    return
                except NoteFetchUnavailable as exc:
                    reply(self, 422, {"status": "failed", "error": str(exc)})
                    return
                reply(self, 202, {"status": "accepted", **result})
                return
            if path == "/v1/dashboard/operation":
                namespace, method, args, kwargs = parse_operation_payload(payload)
                result = RUNTIME.execute(namespace, method, args, kwargs)
                safe_result = _bounded_operation_result(result)
                delivery = None
                if payload.get("deliver_to_feishu"):
                    if not FEISHU_WEBHOOK:
                        raise OperationError("XHS Feishu webhook is not configured")
                    request_id = str(payload.get("request_id") or secrets.token_hex(16))
                    delivery = STORE.enqueue_manual_message(
                        format_result(f"{namespace}.{method}", safe_result), request_id)
                reply(self, 200, {"status": "completed", "operation": f"{namespace}.{method}",
                                  "result": safe_result, "delivery": delivery})
                return
            if path == "/v1/run":
                queries = payload.get("keywords") or DEFAULT_QUERIES
                if not isinstance(queries, list):
                    raise ValueError("keywords_must_be_list")
                result = collect(STORE, SOURCE_ROOT, COOKIE_FILE, [str(x) for x in queries[:20]], limit=FETCH_LIMIT, request_key=str(payload.get("run_key") or ""), delay=max(1, int(os.environ.get("XHS_REQUEST_DELAY", "3"))))
                while STORE.enqueue_pending():
                    pass
                reply(self, 200, {**result, "queue": STORE.status().get("jobs", {})})
                return
            if path == "/v1/recommendations/run":
                reply(self, 200, run_recommendation(payload))
                return
            if path == "/v1/recommendations/retry":
                run_id = str(payload.get("run_id") or "").strip()
                if not run_id:
                    raise ValueError("recommendation_run_id_required")
                if not STORE.retry_recommendation_filter(run_id):
                    reply(self, 409, {"status": "not_retryable", "run_id": run_id})
                    return
                reply(self, 200, {"status": "accepted", "run_id": run_id})
                return
            if path == "/v1/following/sync":
                reply(self, 200, sync_following(payload))
                return
            if path == "/v1/following/screen":
                reply(self, 200, screen_following(payload))
                return
            if path == "/v1/topics":
                if payload.get('action') == 'disable' or payload.get('action') == 'enable':
                    changed = STORE.set_topic_enabled(payload.get('slug'), payload.get('action') == 'enable')
                    reply(self, 200, {'status': 'updated' if changed else 'not_found', 'slug': payload.get('slug')})
                else:
                    reply(self, 200, {'status': 'updated', 'topic': STORE.upsert_topic(payload)})
                return
            if path == "/v1/watch/run":
                reply(self, 200, run_watch(payload))
                return
            if path == "/v1/command":
                reply(self, 200, command_result(payload))
                return
            if path == "/v1/operation":
                namespace, method, args, kwargs = parse_operation_payload(payload)
                result = RUNTIME.execute(namespace, method, args, kwargs)
                reply(self, 200, {"status": "completed", "operation": f"{namespace}.{method}", "result": sanitize(result)})
                return
            if path == "/v1/worker/claim":
                reply(self, 200, {"job": STORE.claim(
                    str(payload.get("worker") or "mac-ai"), lane=payload.get("lane"))})
                return
            if path == "/v1/worker/heartbeat":
                STORE.heartbeat(str(payload.get("job_id")), str(payload.get("lease_token")))
                reply(self, 200, {"status": "extended"})
                return
            if path == "/v1/worker/complete":
                if isinstance(payload.get("decisions"), list):
                    result = {"decisions": payload.get("decisions"), "model": payload.get("model"),
                              "provider": payload.get("provider"), "input_sha256": payload.get("input_sha256")}
                elif isinstance(payload.get("profiles"), list):
                    result = {"profiles": payload.get("profiles"), "model": payload.get("model"),
                              "provider": payload.get("provider"), "input_sha256": payload.get("input_sha256")}
                else:
                    result = {"summary": payload.get("summary"), "model": payload.get("model"),
                              "provider": payload.get("provider"), "input_sha256": payload.get("input_sha256"),
                              "media_analyzed": bool(payload.get("media_analyzed")),
                              "media_requested": payload.get("media_requested", 0),
                              "media_downloaded": payload.get("media_downloaded", 0),
                              "media_errors": payload.get("media_errors", 0),
                              "coverage": payload.get("coverage", "text_only")}
                reply(self, 200, {"status": STORE.complete(str(payload.get("job_id")), str(payload.get("lease_token")), result)})
                return
            if path == "/v1/worker/fail":
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
