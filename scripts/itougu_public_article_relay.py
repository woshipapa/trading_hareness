#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""爱投顾公开圈子文章 → 飞书。

文章由本地微信卡片触发，对文章 ID 调 article/view 拉正文；公开观点流由
view/list 轮询补齐。两者都以圈子 ID 白名单确认来源并发送到同一组飞书出口。
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import itougu_neican_relay as NEICAN

API_BASE = "https://group-api.itougu.com"
ARTICLE_VIEW = "/teach-community/article/view"
VIEW_LIST = "/teach-community/view/list"
SUCCESS = 20000
TARGET_CIRCLES = {
    "1806590848897601536": "尾盘掘金",
    "1661937625084334080": "研习社",
    # 主力行为学的公开文章会从同一张爱投顾公众号表推送；其
    # article/view 返回的真实圈子 ID 为 1661566330475778048。
    "1661566330475778048": "主力行为学",
}
_default_state_dir = Path(os.environ.get("ITOUGU_STATE_FILE", "/Users/papa/codebase/n8n/state/itougu-neican.json")).parent
STATE_FILE = Path(os.environ.get(
    "ITOUGU_PUBLIC_ARTICLE_STATE_FILE",
    str(_default_state_dir / "itougu-public-articles.json"),
))
_article_id_re = re.compile(r"[?&]articleId=(\d+)")
_circle_id_re = re.compile(r"[?&]circleId=(\d+)")
_article_id_json_re = re.compile(r"(?:^|[\"'])articleId(?:[\"']\s*:\s*|=)(\d+)")
MAIN_BEHAVIOR_CIRCLE_ID = "1661566330475778048"


_HEADERS = {"cache": None}


def _auth_headers():
    """article/view 需要认证：无认证服务端只返回全 null 空壳。复用内参同一 token。"""
    if _HEADERS["cache"] is None:
        _HEADERS["cache"] = NEICAN.load_headers()
    h = dict(_HEADERS["cache"])
    h["Content-Type"] = "application/json"
    return h


def public_call(path, body):
    """POST JSON + 认证头调文章详情接口（无认证拿不到正文）。"""
    req = urllib.request.Request(
        API_BASE + path,
        data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers=_auth_headers(),
    )
    with urllib.request.urlopen(req, timeout=25) as response:
        result = json.loads(response.read().decode("utf-8", "ignore"))
    if result.get("code") != SUCCESS:
        raise RuntimeError("文章接口返回 %s" % result.get("code"))
    return result.get("data") or {}


def fetch_article(article_id):
    return public_call(ARTICLE_VIEW, {"articleId": str(article_id)})


def fetch_views(circle_id, page_size=30):
    """Fetch the viewpoint stream behind circle/enter update cards."""
    return (public_call(VIEW_LIST, {
        "circleId": str(circle_id), "pageNum": 1, "pageSize": page_size,
    }).get("rows") or [])


def load_state():
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(state.get("seen"), list):
            if not isinstance(state.get("pending"), dict):
                state["pending"] = {}
            return state
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass
    return {"seen": [], "pending": {}}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["seen"] = state["seen"][-1000:]
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def format_article(circle_name, article, article_url, delivery_label):
    title = "%s · 文章" % circle_name
    if delivery_label:
        title = "[%s] %s" % (delivery_label, title)
    lines = []
    when = article.get("publicTime") or article.get("createTime")
    if when:
        lines.append("🕐 %s" % when)
    headline = str(article.get("title") or "").strip()
    if headline:
        lines.append(headline)
    body = NEICAN.html2text(article.get("content") or "")
    if body:
        lines.append(body)
    # 飞书展示只保留标题和正文；原文 URL 不放进群消息。
    return title, "\n\n".join(lines)


def format_view(circle_name, view, circle_id, delivery_label):
    title = "%s · 观点" % circle_name
    if delivery_label:
        title = "[%s] %s" % (delivery_label, title)
    lines = []
    when = view.get("publicTime") or view.get("createTime") or ""
    if when:
        lines.append("🕐 %s" % when)
    body = NEICAN.html2text(view.get("content") or "")
    if body:
        lines.append(body)
    return title, "\n\n".join(lines)


def view_article_id(view):
    """Extract an article id from a public-circle view payload, if present."""
    for key in ("articleId", "article_id"):
        value = view.get(key)
        if value:
            return str(value)
    for key in ("content", "referContent"):
        value = view.get(key)
        if isinstance(value, dict):
            for nested in ("articleId", "article_id"):
                if value.get(nested):
                    return str(value[nested])
        if not isinstance(value, str):
            continue
        try:
            payload = json.loads(value)
            if isinstance(payload, dict):
                for nested in ("articleId", "article_id"):
                    if payload.get(nested):
                        return str(payload[nested])
        except (TypeError, ValueError):
            match = _article_id_json_re.search(value)
            if match:
                return match.group(1)
    return ""


def after_close():
    """Return true only after 15:00 Asia/Shanghai on a weekday."""
    now = datetime.now(NEICAN.CST)
    return now.weekday() < 5 and now.hour * 60 + now.minute >= 15 * 60


def _retry_delay(attempts):
    return min(300, 30 * (2 ** min(attempts, 4)))


def _queue_pending(state, article_id, article_url, attempts=0):
    now = time.time()
    old = state["pending"].get(article_id, {})
    attempts = max(int(old.get("attempts", 0)), attempts) + 1
    state["pending"][article_id] = {
        "url": article_url,
        "added_at": old.get("added_at", now),
        "attempts": attempts,
        "next_retry_at": now + _retry_delay(attempts),
    }


def _process(article_id, article_url, state, verbose, delivery_label):
    """Return one of sent, done, pending.  Pending is never forwarded."""
    try:
        article = fetch_article(article_id)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        if verbose:
            print("公开文章读取失败(忽略): %s" % exc, flush=True)
        return "pending"
    circle_id = str(article.get("circleId") or "")
    circle_name = TARGET_CIRCLES.get(circle_id)
    # A card can arrive before the article is published.  It has neither a
    # usable circle ID nor a body at that point, so retain only its ID/URL for
    # bounded retries.  A published non-target article is terminally ignored.
    if article.get("isExist") != 1 or not str(article.get("content") or "").strip():
        if verbose:
            print("公开文章待发布：articleId=%s circleId=%s isExist=%s" %
                  (article_id, circle_id or "-", article.get("isExist")), flush=True)
        return "pending"
    if not circle_name:
        if verbose:
            print("公开文章跳过：非目标圈子 articleId=%s circleId=%s" %
                  (article_id, circle_id or "-"), flush=True)
        return "done"
    title, text = format_article(circle_name, article, article_url, delivery_label)
    try:
        for chat_id in NEICAN.article_chat_ids():
            NEICAN.send_feishu(chat_id, title, text, "public-article:%s" % article_id)
    except Exception as exc:
        if verbose:
            print("公开文章飞书发送失败(忽略): %s" % exc, flush=True)
        return "pending"
    if verbose:
        print("✅ 已发飞书 [公开文章] %s (%s)" % (title, article_id), flush=True)
    return "sent"


def trigger_from_push(username, article_url, verbose=False, delivery_label="database"):
    """Process one new card; retain only temporarily unavailable public articles."""
    if username != NEICAN.GH or "/circle/article/detail" not in (article_url or ""):
        return 0
    match = _article_id_re.search(article_url)
    if not match:
        return 0
    article_id = match.group(1)
    state = load_state()
    if article_id in set(state["seen"]):
        return 0
    outcome = _process(article_id, article_url, state, verbose, delivery_label)
    if outcome == "pending":
        _queue_pending(state, article_id, article_url)
    else:
        state["pending"].pop(article_id, None)
        state["seen"].append(article_id)
    save_state(state)
    return 1 if outcome == "sent" else 0


def trigger_from_circle_push(username, circle_url, verbose=False, delivery_label="database"):
    """Handle a circle/enter card using the small-table event as trigger."""
    if username != NEICAN.GH or "/circle/enter" not in (circle_url or ""):
        return 0
    match = _circle_id_re.search(circle_url)
    if not match or match.group(1) != "1661937625084334080":
        return 0
    return poll_views(verbose=verbose, delivery_label=delivery_label)


def retry_pending(verbose=False, delivery_label="database"):
    """Retry only article IDs seen by the WeChat listener."""
    state = load_state()
    now = time.time()
    changed = False
    sent = 0
    for article_id, entry in list(state["pending"].items()):
        if now - float(entry.get("added_at", now)) > 6 * 3600:
            state["pending"].pop(article_id, None)
            changed = True
            continue
        if now < float(entry.get("next_retry_at", 0)):
            continue
        outcome = _process(article_id, entry.get("url", ""), state, verbose, delivery_label)
        changed = True
        if outcome == "pending":
            _queue_pending(state, article_id, entry.get("url", ""), int(entry.get("attempts", 0)))
        else:
            state["pending"].pop(article_id, None)
            state["seen"].append(article_id)
            sent += outcome == "sent"
    if changed:
        save_state(state)
    return int(sent)


def poll_views(verbose=False, delivery_label="poll"):
    """Poll the viewpoint stream for circles that emit circle/enter cards."""
    state = load_state()
    seen = set(state.setdefault("view_seen", []))
    article_seen = set(state.setdefault("article_seen", []))
    sent = 0
    changed = False
    for circle_id, circle_name in TARGET_CIRCLES.items():
        # The endpoint is available for all registered public circles.  Keep
        # the whitelist explicit so adding a new source cannot broaden the
        # polling scope accidentally.
        # 主力行为学只在沪市收盘后轮询，避免盘中把文章预告当成策略输入。
        if circle_id == MAIN_BEHAVIOR_CIRCLE_ID and not after_close():
            continue
        try:
            rows = fetch_views(circle_id)
        except Exception as exc:
            if verbose:
                print("公开观点轮询失败(忽略): %s" % exc, flush=True)
            continue
        for view in rows:
            view_id = str(view.get("viewId") or "")
            article_id = view_article_id(view) if circle_id == MAIN_BEHAVIOR_CIRCLE_ID else ""
            if not view_id or (view_id in seen and (not article_id or article_id in article_seen)):
                continue
            # Do not replay the historical first page on first activation;
            # do include today's intraday backlog so the new listener catches
            # updates that arrived before deployment.
            when = str(view.get("publicTime") or view.get("createTime") or "")
            if not when.startswith(datetime.now(NEICAN.CST).strftime("%Y-%m-%d")):
                seen.add(view_id)
                changed = True
                continue
            if article_id:
                try:
                    article = fetch_article(article_id)
                except (OSError, ValueError, urllib.error.URLError) as exc:
                    if verbose:
                        print("主力行为学文章读取失败(忽略): %s" % exc, flush=True)
                    continue
                if article.get("isExist") != 1 or not str(article.get("content") or "").strip():
                    if verbose:
                        print("主力行为学文章暂不可用：articleId=%s" % article_id, flush=True)
                    continue
                title, text = format_article(circle_name, article, "", delivery_label)
            else:
                title, text = format_view(circle_name, view, circle_id, delivery_label)
            try:
                for chat_id in NEICAN.article_chat_ids():
                    NEICAN.send_feishu(chat_id, title, text, "public-view:%s" % view_id)
            except Exception as exc:
                if verbose:
                    print("公开观点飞书发送失败(忽略): %s" % exc, flush=True)
                continue
            seen.add(view_id)
            if article_id:
                article_seen.add(article_id)
            changed = True
            sent += 1
            if verbose:
                print("✅ 已发飞书 [公开观点] %s (%s)" % (title, view_id), flush=True)
    if changed:
        state["view_seen"] = list(seen)[-1000:]
        state["article_seen"] = list(article_seen)[-1000:]
        save_state(state)
    return sent
