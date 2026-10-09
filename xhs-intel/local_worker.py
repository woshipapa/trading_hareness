#!/usr/bin/env python3
"""Mac-side worker: claim edge jobs and summarize them with Paper-KB Codex."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path
from threading import Event, Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent
PAPER_KB = Path(os.environ.get("XHS_PAPER_KB_ROOT", str(ROOT.parent.parent / "literature_maps" / "paper_kb")))
if str(PAPER_KB) not in sys.path:
    sys.path.insert(0, str(PAPER_KB))
import codex_provider  # noqa: E402
from common import request_json, error_code  # noqa: E402


EDGE_URL = os.environ.get("XHS_EDGE_URL", "http://127.0.0.1:18790").rstrip("/")
EDGE_TOKEN = os.environ.get("XHS_COLLECTOR_TOKEN", "")
WORKER_ID = os.environ.get("XHS_AI_WORKER_ID", f"mac-{os.getpid()}")
POLL_SECONDS = max(2, int(os.environ.get("XHS_AI_POLL_SECONDS", "10")))
TIMEOUT = max(60, int(os.environ.get("XHS_AI_TIMEOUT", "900")))
HEALTH_HOST = os.environ.get("XHS_AI_WORKER_HOST", "127.0.0.1")
HEALTH_PORT = int(os.environ.get("XHS_AI_WORKER_PORT", "8793"))
FILTER_BATCH_SIZE = max(1, min(20, int(os.environ.get("XHS_AI_FILTER_BATCH_SIZE", "10"))))
MAX_IMAGES_PER_NOTE = max(1, min(18, int(os.environ.get("XHS_AI_MAX_IMAGES_PER_NOTE", "18"))))
MAX_BATCH_IMAGES = max(1, min(32, int(os.environ.get("XHS_AI_MAX_BATCH_IMAGES", "24"))))
MAX_IMAGE_BYTES = max(256 * 1024, min(12 * 1024 * 1024,
                                      int(os.environ.get("XHS_AI_MAX_IMAGE_BYTES", str(8 * 1024 * 1024)))))
IMAGE_TIMEOUT = max(5, min(60, int(os.environ.get("XHS_AI_IMAGE_TIMEOUT", "20"))))
_IMAGE_HOST = "ci.xiaohongshu.com"
STATE = {"status": "starting", "last_job": None, "last_jobs": {}, "last_error": None,
         "completed": 0, "failed": 0}


def make_prompt(job):
    notes = job.get("notes") or []
    lines = [
        "你是做 AI infra / systems for AI 的中文研究编辑。",
        "请把下面的小红书公开笔记整理成适合飞书群阅读的每日情报摘要。",
        "读者关注 GPU、训练系统、推理、编译器、互联、存储和 AI 工程实践。",
        "要求：先列 3-8 条关键结论；按主题合并；明确区分原文事实、作者观点和你的推断；",
        "无法核验的数字、传闻和营销表述标为‘待核验’；每条保留标题、作者、时间、原文链接；",
        "最后给出值得继续跟踪的方向和下一步检索词。输出纯 Markdown，3000 字以内。",
        f"任务：{job.get('job_id', '')}", "",
    ]
    for index, note in enumerate(notes, 1):
        image_count = len(note.get("image_urls") or [])
        lines.extend([
            f"### 笔记 {index}",
            f"标题：{note.get('title', '')}",
            f"作者：{note.get('author', '')}",
            f"时间：{note.get('published_at', '')}",
            f"链接：{note.get('url', '')}",
            f"正文：{note.get('text', '')}",
            f"图片：{image_count} 张；图片会作为视觉输入提供，请把图片中可读文字、图表和关键视觉信息单独标注为‘图片观察’，不确定内容标为待核验。",
            "",
        ])
    return "\n".join(lines)[:100000]


def make_single_note_prompt(job):
    note = (job.get("notes") or [{}])[0]
    image_count = len(note.get("image_urls") or [])
    return "\n".join([
        "你是做 AI infra / systems for AI 的中文研究编辑。",
        "请独立分析下面这一篇小红书公开笔记，输出可直接阅读的 Markdown。",
        "结构必须包括：内容摘要；原文事实、作者观点、编辑推断；与 AI infra / systems、",
        "模型或科研的相关性；适合账号继续创作的内容角度；待核验的说法或数字；后续检索词。",
        "如果文章与这些领域弱相关，要直接说明，不要强行建立联系。",
        "不得把作者自述当成已核验事实；保留标题、作者、时间和原文链接。输出 3000 字以内。",
        f"图片：{image_count} 张。图片会作为视觉输入提供；请把图片中可读文字、图表和关键视觉信息单独标注为‘图片观察’，不要把看不清的内容当成事实。",
        f"任务：{job.get('job_id', '')}",
        f"标题：{note.get('title', '')}",
        f"作者：{note.get('author', '')}",
        f"时间：{note.get('published_at', '')}",
        f"链接：{note.get('url', '')}",
        f"正文：{note.get('text', '')}",
    ])[:100000]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


def _allowed_image_url(value):
    parsed = urlparse(str(value or ""))
    return (parsed.scheme == "https" and parsed.hostname == _IMAGE_HOST
            and parsed.port in (None, 443) and not parsed.username and not parsed.password)


def _download_image(url):
    """Download one XHS CDN image with an allowlist and hard byte limit."""
    current = str(url or "")
    opener = build_opener(_NoRedirect())
    for _ in range(3):
        if not _allowed_image_url(current):
            raise ValueError("image_host_not_allowed")
        request = Request(current, headers={"User-Agent": "Mozilla/5.0"}, method="GET")
        try:
            response = opener.open(request, timeout=IMAGE_TIMEOUT)
        except HTTPError as exc:
            try:
                if exc.code not in {301, 302, 303, 307, 308}:
                    raise RuntimeError("image_fetch_failed") from exc
                location = exc.headers.get("Location")
            finally:
                exc.close()
            if not location:
                raise RuntimeError("image_redirect_missing")
            current = urljoin(current, location)
            continue
        except (OSError, URLError) as exc:
            raise RuntimeError("image_fetch_failed") from exc
        try:
            if response.headers.get_content_type() not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
                raise ValueError("image_content_type_not_allowed")
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_IMAGE_BYTES:
                raise ValueError("image_too_large")
            data = response.read(MAX_IMAGE_BYTES + 1)
        finally:
            response.close()
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("image_too_large")
        if not data:
            raise ValueError("image_empty")
        return data
    raise RuntimeError("image_too_many_redirects")


def _image_data_url(data):
    """Normalize remote media before it enters the model request."""
    try:
        from PIL import Image
        image = Image.open(io.BytesIO(data))
        image.seek(0)
        image = image.convert("RGB")
        image.thumbnail((1600, 1600))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=82, optimize=True)
        data = output.getvalue()
    except Exception as exc:  # noqa: BLE001 - malformed media is per-image failure
        raise ValueError("image_decode_failed") from exc
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


def _load_note_images(notes, *, max_images=MAX_BATCH_IMAGES, per_note=MAX_IMAGES_PER_NOTE):
    """Return in-memory vision inputs and aggregate sanitized media counters."""
    media = []
    errors = 0
    requested = 0
    for note in notes or []:
        note_id = str(note.get("note_id") or note.get("_candidate_id") or "")
        urls = list(note.get("image_urls") or [])[:per_note]
        for index, url in enumerate(urls):
            if len(media) >= max_images:
                break
            requested += 1
            try:
                media.append({
                    "note_id": note_id,
                    "index": index + 1,
                    "image_url": _image_data_url(_download_image(url)),
                })
            except (OSError, RuntimeError, ValueError, TypeError):
                errors += 1
    return media, {"requested": requested, "downloaded": len(media), "errors": errors}


def _multimodal_items(prompt, media):
    if not media:
        return prompt
    content = [{"type": "input_text", "text": prompt}]
    for item in media:
        content.append({
            "type": "input_text",
            "text": f"图片观察输入：note_id={item['note_id']}，第 {item['index']} 张图片。",
        })
        content.append({"type": "input_image", "image_url": item["image_url"], "detail": "low"})
    return [{"role": "user", "content": content}]


def _request_summary(prompt, *, media=None, media_stats=None):
    base, _model, _effort = codex_provider.load_endpoint()
    keys = codex_provider.ordered_keys()
    if not keys:
        raise RuntimeError("codex-teleai key pool is unavailable")
    request_input = _multimodal_items(prompt, media or [])
    text, model = codex_provider.request(
        request_input,
        base=base,
        keys=keys,
        chain=codex_provider.default_chain(),
        timeout=TIMEOUT,
        retries=2,
    )
    text = text.strip()
    if not text or len(text) > 20000:
        raise ValueError("invalid_summary_length")
    return {
        "summary": text,
        "model": model,
        "provider": "paper-kb/codex_provider",
        "input_sha256": hashlib.sha256(
            (prompt + "\n" + "\n".join(item["image_url"] for item in media or [])).encode()
        ).hexdigest(),
        "media_analyzed": bool(media),
        "media_requested": int((media_stats or {}).get("requested", 0)),
        "media_downloaded": int((media_stats or {}).get("downloaded", 0)),
        "media_errors": int((media_stats or {}).get("errors", 0)),
        "coverage": "text_and_images" if media else "text_only",
    }


def make_filter_prompt(job):
    """Build a bounded classifier prompt without signed upstream material."""
    policy = job.get("policy") or {}
    topics = policy.get("topics") or []
    lines = [
        "你是 AI infrastructure / systems for AI 的内容筛选器。",
        "只根据给出的标题、作者、时间和正文判断，不能臆造原文没有的事实。",
        "目标读者关注 AI 基础设施、训练系统、推理、编译器、模型、系统工程和科研。",
        "对消费、生活方式、泛营销和无关内容判为 exclude；不确定但可能相关判为 review。",
        "只输出一个 JSON 对象，不要 Markdown、解释或代码围栏。",
        '格式：{"decisions":[{"candidate_id":"...","decision":"include|review|exclude","topics":[{"topic_id":"ai_infra","score":0.0}],"relevance_score":0.0,"confidence":0.0,"reason":"不超过200字","evidence":["关键词"],"risk_flags":[]}]}',
        "可用 Topic 策略：" + json.dumps(topics, ensure_ascii=False),
        "任务：" + str(job.get("job_id", "")),
        "",
    ]
    for index, note in enumerate(job.get("notes") or [], 1):
        lines.extend([
            f"候选 {index}",
            f"candidate_id：{note.get('_candidate_id') or note.get('revision') or ''}",
            f"标题：{note.get('title', '')}",
            f"作者：{note.get('author', '')}",
            f"时间：{note.get('published_at', '')}",
            f"正文：{str(note.get('text', ''))[:5000]}",
            f"链接：{note.get('url', '')}",
            "",
        ])
    return "\n".join(lines)[:100000]


def make_digest_prompt(job):
    """Per-topic daily digest with delta marking against the previous digest."""
    policy = job.get("policy") or {}
    topic_names = "、".join(
        f"{item.get('name', '')}({item.get('slug', '')})"
        for item in policy.get("topics") or [])
    lines = [
        "你是做 AI infra / systems for AI 的中文研究编辑，负责写一份个人学习用的每日情报简报。",
        f"简报日期:{job.get('digest_date', '')}。输出纯 Markdown，结构固定为:",
        "1. `## 今日导读`:3-6 条全局要点，每条一句话并标注所属主题;",
        "2. 按主题分节(`## 主题:<名称>`)，只为有内容的主题建节;每节 2-6 条,",
        "   每条包含结论、依据(原文事实、作者观点、编辑推断分开表述)和 标题+作者+原文链接;",
        "3. `## 与昨日对比`:相对昨日简报的新话题、进展与反转;无昨日简报则写'首次记录';",
        "4. `## 待核验`:集中列出可疑数字、传闻与营销表述;",
        "5. `## 继续跟踪`:值得跟进的方向，以及建议加入主题检索词的新关键词。",
        "不得虚构原文没有的事实;无法核验的内容必须标注'待核验'。全文 4000 字以内。",
        f"可用主题:{topic_names}",
        f"任务:{job.get('job_id', '')}",
        "",
        "=== 昨日简报(仅用于对比，勿重复照抄) ===",
        str(job.get("previous_digest") or "(无)")[:4000],
        "",
        "=== 今日候选笔记 ===",
    ]
    for index, note in enumerate(job.get("notes") or [], 1):
        topics_tag = "、".join(
            str(item.get("topic_id") or "") for item in (note.get("_topics") or [])
            if isinstance(item, dict) and item.get("topic_id")) or "未分类"
        lines.extend([
            f"### 笔记 {index}(主题:{topics_tag})",
            f"标题:{note.get('title', '')}",
            f"作者:{note.get('author', '')}",
            f"时间:{note.get('published_at', '')}",
            f"链接:{note.get('url', '')}",
            f"正文:{str(note.get('text', ''))[:3000]}",
            "",
        ])
    return "\n".join(lines)[:100000]


def summarize(job):
    notes = list(job.get("notes") or [])
    media, stats = _load_note_images(notes)
    return _request_summary(make_prompt(job), media=media, media_stats=stats)


def build_digest(job):
    # The digest covers up to dozens of already-screened notes; it stays
    # text-only so one job cannot fan out into hundreds of image downloads.
    return _request_summary(make_digest_prompt(job))


def analyze_single_note(job):
    notes = list(job.get("notes") or [])[:1]
    media, stats = _load_note_images(notes, max_images=MAX_IMAGES_PER_NOTE)
    return _request_summary(make_single_note_prompt(job), media=media, media_stats=stats)


def _parse_json_response(text, key='decisions'):
    text = str(text or '').strip()
    if text.startswith('```'):
        text = text.strip('`')
        if text.lstrip().startswith('json'):
            text = text.lstrip()[4:].lstrip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError('invalid_filter_json') from exc
    if isinstance(value, list):
        value = {key: value}
    if not isinstance(value, dict) or not isinstance(value.get(key), list):
        raise ValueError('invalid_filter_schema')
    return value


def classify(job):
    base, _model, _effort = codex_provider.load_endpoint()
    keys = codex_provider.ordered_keys()
    if not keys:
        raise RuntimeError("codex-teleai key pool is unavailable")
    notes = list(job.get("notes") or [])
    expected = {
        str(note.get("_candidate_id") or note.get("revision") or note.get("note_id") or "")
        for note in notes
    }
    if not notes or "" in expected:
        raise ValueError("invalid_filter_candidates")
    decisions = []
    prompts = []
    model = ""
    for offset in range(0, len(notes), FILTER_BATCH_SIZE):
        chunk = notes[offset:offset + FILTER_BATCH_SIZE]
        chunk_job = dict(job)
        chunk_job["job_id"] = f"{job.get('job_id', '')}/part-{offset // FILTER_BATCH_SIZE + 1}"
        chunk_job["notes"] = chunk
        prompt = make_filter_prompt(chunk_job)
        prompts.append(prompt)
        text, model = codex_provider.request(
            prompt, base=base, keys=keys, chain=codex_provider.default_chain(),
            timeout=TIMEOUT, retries=2,
        )
        parsed = _parse_json_response(text)
        part = parsed["decisions"]
        part_ids = [str(item.get("candidate_id") or item.get("note_id") or "")
                    for item in part if isinstance(item, dict)]
        if len(part_ids) != len(set(part_ids)) or set(part_ids) != {
                str(note.get("_candidate_id") or note.get("revision") or note.get("note_id") or "")
                for note in chunk
        }:
            raise ValueError("invalid_filter_batch_coverage")
        decisions.extend(part)
    if {str(item.get("candidate_id") or item.get("note_id") or "") for item in decisions} != expected:
        raise ValueError("invalid_filter_coverage")
    return {
        "decisions": decisions,
        "model": model,
        "provider": "paper-kb/codex_provider",
        "input_sha256": hashlib.sha256("\n".join(prompts).encode()).hexdigest(),
    }


def make_profile_filter_prompt(job):
    lines = [
        "你是 AI infra / systems for AI 账号筛选器。",
        "根据账号昵称、简介和最近作品，判断是否值得加入我们的内部监控列表。",
        "关注 GPU、训练系统、推理、编译器、模型、系统工程和 AI 科研；泛消费、生活方式和纯招聘广告排除。",
        "只输出 JSON，不要 Markdown。必须覆盖每个 user_id。",
        '格式：{"profiles":[{"user_id":"...","decision":"include|review|exclude","topics":[{"topic_id":"ai_infra","score":0.0}],"score":0.0,"confidence":0.0,"reason":"不超过300字","recent_note_ids":[]}]}',
        "任务：" + str(job.get("job_id", "")),
        "",
    ]
    for profile in job.get('profiles') or []:
        lines.extend([
            "user_id：" + str(profile.get('user_id', '')),
            "昵称：" + str(profile.get('nickname', '')),
            "简介：" + str(profile.get('description', ''))[:1000],
            "最近作品：" + json.dumps(profile.get('recent_notes') or [], ensure_ascii=False)[:6000],
            "",
        ])
    return "\n".join(lines)[:100000]


def classify_profiles(job):
    prompt = make_profile_filter_prompt(job)
    base, _model, _effort = codex_provider.load_endpoint()
    keys = codex_provider.ordered_keys()
    if not keys:
        raise RuntimeError("codex-teleai key pool is unavailable")
    text, model = codex_provider.request(
        prompt, base=base, keys=keys, chain=codex_provider.default_chain(),
        timeout=TIMEOUT, retries=2,
    )
    parsed = _parse_json_response(text, key='profiles')
    return {"profiles": parsed.get("profiles") or [],
            "model": model, "provider": "paper-kb/codex_provider",
            "input_sha256": hashlib.sha256(prompt.encode()).hexdigest()}


def call_edge(path, payload=None, timeout=30):
    return request_json(f"{EDGE_URL}{path}", payload, token=EDGE_TOKEN,
                        header="X-XHS-Collector-Token", timeout=timeout)


def run_with_heartbeat(job, task):
    """Keep the fenced Edge lease alive while a model request is in flight."""
    stopped = Event()
    interval = max(10, min(60, int(job.get("lease_seconds") or 180) // 3))

    def heartbeat_loop():
        while not stopped.wait(interval):
            try:
                call_edge('/v1/worker/heartbeat', {
                    'job_id': job['job_id'], 'lease_token': job['lease_token'],
                })
            except Exception as exc:  # noqa: BLE001
                STATE['last_error'] = error_code(exc)

    thread = Thread(target=heartbeat_loop, name='xhs-ai-heartbeat', daemon=True)
    thread.start()
    try:
        return task(job)
    finally:
        stopped.set()
        thread.join(timeout=1)


def process_job(job):
    if job.get("job_type") in ("classify_recommendations", "classify_notes"):
        return run_with_heartbeat(job, classify)
    if job.get("job_type") == "classify_profiles":
        return run_with_heartbeat(job, classify_profiles)
    if job.get("job_type") == "single_note_analysis":
        return run_with_heartbeat(job, analyze_single_note)
    if job.get("job_type") == "daily_digest":
        return run_with_heartbeat(job, build_digest)
    return run_with_heartbeat(job, summarize)


def loop(lane='batch'):
    STATE["status"] = "running"
    while True:
        job = None
        try:
            worker = f'{WORKER_ID}:{lane}'
            response = call_edge("/v1/worker/claim", {"worker": worker, "lane": lane})
            # A successful claim request proves the edge tunnel and token are
            # healthy even when the queue is empty. Clear a transient error so
            # the health endpoint reflects the current connection state.
            STATE["last_error"] = None
            job = response.get("job") if isinstance(response, dict) else None
            if not job:
                time.sleep(POLL_SECONDS)
                continue
            STATE["last_job"] = job.get("job_id")
            STATE["last_jobs"][lane] = job.get("job_id")
            result = process_job(job)
            call_edge("/v1/worker/complete", {"job_id": job["job_id"], "lease_token": job["lease_token"], **result}, timeout=TIMEOUT + 30)
            STATE["completed"] += 1
            STATE["last_error"] = None
        except Exception as exc:  # noqa: BLE001
            STATE["last_error"] = error_code(exc)
            STATE["failed"] += 1
            if job and job.get("job_id") and job.get("lease_token"):
                try:
                    call_edge("/v1/worker/fail", {"job_id": job["job_id"], "lease_token": job["lease_token"], "error": error_code(exc)})
                except Exception:
                    pass
            time.sleep(min(60, POLL_SECONDS * 2))


class HealthHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def do_GET(self):  # noqa: N802
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"status": "ok", "worker": WORKER_ID, **STATE}, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    server = ThreadingHTTPServer((HEALTH_HOST, HEALTH_PORT), HealthHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    Thread(target=loop, args=('interactive',), name='xhs-ai-interactive', daemon=True).start()
    loop('batch')


if __name__ == "__main__":
    main()
