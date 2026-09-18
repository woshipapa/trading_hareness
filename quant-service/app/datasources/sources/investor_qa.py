"""Investor Q&A platforms: CNInfo 互动易 (SZ/BJ) and SSE e互动 (SH).

Both platforms publish a question and, later, the company's answer.  An item
becomes evidence when the *answer* is public, so ``answered_at`` is the
event time.  CNInfo gives epoch milliseconds; SSE renders relative times
("3小时前") that are resolved against the capture clock, which bounds them
from above and keeps the event time point-in-time safe.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from ..http import ashare_symbol, request_json, request_text


CN_TZ = ZoneInfo("Asia/Shanghai")
QA_PROVIDERS: dict[str, dict[str, str]] = {
    "cninfo_irm": {"label": "巨潮互动易", "upstream_site": "irm.cninfo.com.cn"},
    "sse_einteract": {"label": "上证e互动", "upstream_site": "sns.sseinfo.com"},
}
MAX_TEXT_CHARS = 2000
_TAGS = re.compile(r"<[^>]+>")


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub("", str(value or "")))).strip()


def _millis(value: Any) -> datetime | None:
    try:
        millis = float(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc) if millis > 0 else None


def _qa(provider: str, qa_id: Any, symbol: str | None, company: str, question: str, answer: str,
        asked_at: datetime | None, answered_at: datetime | None, questioner: str, url: str | None) -> dict[str, Any] | None:
    identity = str(qa_id or "").strip()
    if not identity or symbol is None or not question or not answer or answered_at is None:
        return None
    return {
        "provider": provider, "qa_id": identity, "symbol": symbol, "company": company,
        "question": question[:MAX_TEXT_CHARS], "answer": answer[:MAX_TEXT_CHARS],
        "asked_at": asked_at.isoformat() if asked_at else None, "answered_at": answered_at.isoformat(),
        "questioner": questioner, "url": url, "upstream_site": QA_PROVIDERS[provider]["upstream_site"],
    }


# -- CNInfo 互动易 -------------------------------------------------------------

def normalize_cninfo(payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    rows = []
    for item in (payload or {}).get("results") or []:
        if not isinstance(item, Mapping):
            continue
        qa_id = item.get("indexId")
        row = _qa(
            "cninfo_irm", qa_id, ashare_symbol(item.get("stockCode")),
            str(item.get("companyShortName") or "").strip(), _text(item.get("mainContent")),
            _text(item.get("attachedContent")), _millis(item.get("pubDate")),
            _millis(item.get("attachedPubDate")), str(item.get("authorName") or "").strip(),
            f"https://irm.cninfo.com.cn/ircs/question/questionDetail?questionId={qa_id}" if qa_id else None,
        )
        if row:
            rows.append(row)
    return rows


async def fetch_cninfo(page: int = 1, page_size: int = 50) -> list[dict[str, Any]]:
    """Latest answered questions across all companies, newest first."""
    payload = await request_json("POST", "https://irm.cninfo.com.cn/newircs/index/search", data={
        "pageNo": str(max(1, page)), "pageSize": str(max(1, min(page_size, 100))),
        "searchTypes": "11,", "market": "", "industry": "", "stockCode": "",
    })
    if not isinstance(payload, Mapping):
        raise ValueError("CNInfo IRM response is not an object")
    return normalize_cninfo(payload)


# -- SSE e互动 ----------------------------------------------------------------

_ITEM = re.compile(r'<div class="m_feed_item" id="item-(\d+)">')
_FEED_TEXT = re.compile(r'<div class="m_feed_txt"[^>]*>(.*?)</div>', re.S)
_FEED_FROM = re.compile(r'<div class="m_feed_from"[^>]*>\s*<span>([^<]*)</span>', re.S)
_ASKER = re.compile(r'rel="face"[^>]*title="([^"]*)"', re.S)
_COMPANY_TAG = re.compile(r':\s*([^()（）<]+)[(（](\d{6})[)）]')


def resolve_sse_time(text: str, observed_at: datetime) -> datetime | None:
    """Resolve SSE's rendered time against the capture clock (Shanghai)."""
    value = text.strip()
    local_now = observed_at.astimezone(CN_TZ)
    if match := re.fullmatch(r"(\d+)\s*分钟前", value):
        return observed_at - timedelta(minutes=int(match.group(1)))
    if match := re.fullmatch(r"(\d+)\s*小时前", value):
        return observed_at - timedelta(hours=int(match.group(1)))
    if value in {"刚刚", "刚才"}:
        return observed_at
    for prefix, days in (("今天", 0), ("昨天", 1)):
        if value.startswith(prefix) and (match := re.search(r"(\d{1,2}):(\d{2})", value)):
            day = local_now.date() - timedelta(days=days)
            return datetime(day.year, day.month, day.day, int(match.group(1)), int(match.group(2)), tzinfo=CN_TZ)
    if match := re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})", value):
        year, month, day, hour, minute = (int(part) for part in match.groups())
        return datetime(year, month, day, hour, minute, tzinfo=CN_TZ)
    if match := re.fullmatch(r"(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})", value):
        month, day, hour, minute = (int(part) for part in match.groups())
        return datetime(local_now.year, month, day, hour, minute, tzinfo=CN_TZ)
    return None


def normalize_sse(page_html: str, observed_at: datetime) -> list[dict[str, Any]]:
    rows = []
    starts = [(match.start(), match.group(1)) for match in _ITEM.finditer(page_html)]
    for index, (start, item_id) in enumerate(starts):
        block = page_html[start:starts[index + 1][0] if index + 1 < len(starts) else len(page_html)]
        texts = _FEED_TEXT.findall(block)
        times = _FEED_FROM.findall(block)
        if len(texts) < 2 or len(times) < 2:
            continue  # unanswered question
        question_html = texts[0]
        tag = _COMPANY_TAG.search(_text(question_html.split("</a>", 1)[0]) if "</a>" in question_html else "")
        if not tag:
            continue
        company, code = tag.group(1).strip(), tag.group(2)
        question = _text(question_html.split("</a>", 1)[1])
        asker = _ASKER.search(block)
        answered_at = resolve_sse_time(_text(times[1]), observed_at)
        row = _qa(
            "sse_einteract", item_id, ashare_symbol(code, "SH"), company, question, _text(texts[1]),
            resolve_sse_time(_text(times[0]), observed_at),
            min(answered_at, observed_at) if answered_at else None,
            html.unescape(asker.group(1)) if asker else "", f"https://sns.sseinfo.com/question.do?id={item_id}",
        )
        if row:
            rows.append(row)
    return rows


async def fetch_sse(observed_at: datetime, page: int = 1, page_size: int = 30) -> list[dict[str, Any]]:
    """Latest answered questions (``type=11``), newest answer first."""
    text = await request_text("GET", "https://sns.sseinfo.com/ajax/feeds.do", params={
        "type": "11", "pageSize": str(max(1, min(page_size, 50))), "lastid": "-1", "show": "1", "page": str(max(1, page)),
    })
    return normalize_sse(text, observed_at)


def qa_events(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    events = []
    for row in rows:
        label = QA_PROVIDERS[row["provider"]]["label"]
        events.append({
            "ts_code": row["symbol"], "event_type": "investor_qa", "published_at": row["answered_at"],
            "title": f"{label}：{row['question'][:80]}", "url": row.get("url"),
            "event_identity_key": f"{row['provider']}:investor_qa:{row['qa_id']}",
            "raw": {"capability": "investor_qa", **dict(row)},
        })
    return events


__all__ = [
    "QA_PROVIDERS", "fetch_cninfo", "fetch_sse", "normalize_cninfo", "normalize_sse", "qa_events",
    "resolve_sse_time",
]
