#!/usr/bin/env python3
"""Mac-side worker: claim edge jobs and summarize them with Paper-KB Codex."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from threading import Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
STATE = {"status": "starting", "last_job": None, "last_error": None, "completed": 0, "failed": 0}


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
        lines.extend([
            f"### 笔记 {index}",
            f"标题：{note.get('title', '')}",
            f"作者：{note.get('author', '')}",
            f"时间：{note.get('published_at', '')}",
            f"链接：{note.get('url', '')}",
            f"正文：{note.get('text', '')}",
            "",
        ])
    return "\n".join(lines)[:100000]


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


def summarize(job):
    prompt = make_prompt(job)
    base, _model, _effort = codex_provider.load_endpoint()
    keys = codex_provider.ordered_keys()
    if not keys:
        raise RuntimeError("codex-teleai key pool is unavailable")
    text, model = codex_provider.request(
        prompt,
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
        "input_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
    }


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
    prompt = make_filter_prompt(job)
    base, _model, _effort = codex_provider.load_endpoint()
    keys = codex_provider.ordered_keys()
    if not keys:
        raise RuntimeError("codex-teleai key pool is unavailable")
    text, model = codex_provider.request(
        prompt, base=base, keys=keys, chain=codex_provider.default_chain(),
        timeout=TIMEOUT, retries=2,
    )
    parsed = _parse_json_response(text)
    return {
        "decisions": parsed["decisions"],
        "model": model,
        "provider": "paper-kb/codex_provider",
        "input_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
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


def loop():
    STATE["status"] = "running"
    while True:
        job = None
        try:
            response = call_edge("/v1/worker/claim", {"worker": WORKER_ID})
            # A successful claim request proves the edge tunnel and token are
            # healthy even when the queue is empty. Clear a transient error so
            # the health endpoint reflects the current connection state.
            STATE["last_error"] = None
            job = response.get("job") if isinstance(response, dict) else None
            if not job:
                time.sleep(POLL_SECONDS)
                continue
            STATE["last_job"] = job.get("job_id")
            if job.get("job_type") == "classify_recommendations":
                result = classify(job)
            elif job.get("job_type") == "classify_profiles":
                result = classify_profiles(job)
            else:
                result = summarize(job)
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
    loop()


if __name__ == "__main__":
    main()
