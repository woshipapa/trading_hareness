"""Research-only extraction of Xiaojie relay messages.

The relay ledger contains a mixture of text, image, post and system messages.
This module turns available text and post rich-text elements into auditable
labels.  It does not infer a trade, add a provider value to a live path, or
use a timestamp written inside a message as the strategy-available time.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import re
from typing import Any, Iterable, Mapping, Sequence


TAG_PATTERNS: dict[str, re.Pattern[str]] = {
    "qianlong": re.compile(r"潜龙出海|潜龙"),
    "volume_confirmation": re.compile(r"标志性?K|放量突破|二次放量|放量反包|放量大涨|放量拉升"),
    "pullback_support": re.compile(r"回踩|回抽|支撑|低吸|均线粘合|沿5日线|沿五日线"),
    "pressure_or_overhead": re.compile(r"压力|前高|套牢|获利盘|筹码消化|箱体上沿|抛压"),
    "fundamental_quality": re.compile(r"基本面|业绩|利润|订单|现金流|主营|逻辑|预期|落地|业务占比|财报"),
    "sector_context": re.compile(r"板块|轮动|主线|科技|PCB|液冷|交换机|半导体|创新药|光纤"),
    "risk_exit": re.compile(r"跌破|减仓|清仓|出货|止盈|止损|不及预期|注意|不行|不足|兑现|防守|承压"),
    "position_size": re.compile(r"一成|两成|三成|五成|仓位|底仓|加仓|减仓|分仓|10-20份|10～20份"),
    "market_regime": re.compile(r"指数|大盘|缩量|放量|低开|高开|成交量|量能|分歧|冰点|情绪"),
    "trend_structure": re.compile(r"5日线|五日线|均线|趋势|箱体|突破|站稳|震荡|标志性K"),
}

_INSTRUCTOR_MARKER = re.compile(r"【(?:讲师】小杰|杰奏大师)】?")
_REPORT_HINT = re.compile(r"深度复盘|短线和板块复盘|低风偏复盘|板块复盘|盘面分析|竞价")
_NEGATIVE_CONFIRMATION = re.compile(
    r"没有标志性K|没有放量标志性K|均线粘合不够标准|基本面不行|个股强度不够|形态不太标准|技术面有点走弱|偏向出货|不符合"
)
_CONDITIONAL_CONFIRMATION = re.compile(r"不过|但是|看能否|需要注意|压力|预期|只是|理论上|思路可以")


def _message(record: Mapping[str, Any]) -> Mapping[str, Any]:
    value = record.get("message")
    return value if isinstance(value, Mapping) else record


def _rich_text(value: Any) -> str:
    if isinstance(value, Mapping):
        if value.get("tag") == "text":
            return str(value.get("text") or "")
        if "zh_cn" in value:
            return _rich_text(value.get("zh_cn"))
        if "content_v2" in value:
            return _rich_text(value.get("content_v2"))
        if "content" in value:
            return _rich_text(value.get("content"))
        return ""
    if isinstance(value, list):
        return "".join(_rich_text(item) for item in value)
    return ""


def _plain_text(record: Mapping[str, Any]) -> str | None:
    message = _message(record)
    message_type = str(message.get("msg_type") or "")
    if message_type not in {"text", "post"}:
        return None
    body = message.get("body")
    body = body if isinstance(body, Mapping) else {}
    content = body.get("content")
    if isinstance(content, Mapping):
        payload = content
    elif isinstance(content, str):
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            return None
    else:
        return None
    if message_type == "post":
        text = _rich_text(payload)
    else:
        text = payload.get("text") if isinstance(payload, Mapping) else None
    return text if isinstance(text, str) and text.strip() else None


def _available_at(record: Mapping[str, Any]) -> str | None:
    value = record.get("created_at")
    if value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            pass
    value = record.get("source_create_time")
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).isoformat()


def _split_reply(text: str) -> tuple[str | None, str | None]:
    if "--------" not in text:
        return None, None
    header, answer = text.split("--------", 1)
    answer = answer.split("——", 1)[0].strip()
    lines = [line.strip() for line in header.splitlines() if line.strip()]
    reply_line = next((line for line in lines if line.startswith("回复")), None)
    if reply_line is None:
        return None, answer
    # Keep the original reply context when a question contains punctuation.
    # It is evidence for review, not a canonical natural-language parse.
    question = re.sub(r"^回复\s*", "", reply_line).strip()
    return question or None, answer or None


def _role(source_key: str, text: str) -> str:
    if _INSTRUCTOR_MARKER.search(text) and "--------" in text:
        return "instructor_reply"
    if _INSTRUCTOR_MARKER.search(text):
        return "instructor_statement"
    if source_key.startswith("relay_"):
        return "participant_message"
    if _REPORT_HINT.search(text) or text.lstrip().startswith(("小杰:", "张益纬:")):
        return "night_report_or_review"
    return "other_text"


def _stance(answer: str | None) -> str:
    if not answer:
        return "unclassified"
    if _NEGATIVE_CONFIRMATION.search(answer):
        return "not_confirmed_or_risk"
    if re.search(r"符合模式|符合形态|符合二次放量|是符合模式", answer):
        return "confirmed_conditional" if _CONDITIONAL_CONFIRMATION.search(answer) else "confirmed"
    if re.search(r"支撑|回踩|回抽|低吸|看能否|等待|沿5日线|沿五日线", answer):
        return "support_or_wait"
    if re.search(r"减仓|止盈|止损|清仓|出货|注意|防守|承压", answer):
        return "risk_or_exit"
    if re.search(r"一成|两成|三成|仓位|底仓|分仓", answer):
        return "position_guidance"
    return "observation"


def distill_record(record: Mapping[str, Any], *, known_names: Sequence[str] = ()) -> dict[str, Any]:
    """Return one deterministic, point-in-time extraction for a relay row."""
    message = _message(record)
    text = _plain_text(record)
    base: dict[str, Any] = {
        "source_key": record.get("source_key"),
        "source_chat_id": record.get("source_chat_id"),
        "source_message_id": record.get("source_message_id") or message.get("message_id"),
        "available_at": _available_at(record),
        "message_type": message.get("msg_type"),
        "content_status": (
            "parseable_rich_text" if text is not None and message.get("msg_type") == "post"
            else "parseable_text" if text is not None else "unavailable_non_text"
        ),
        "role": "unavailable_non_text" if text is None else _role(str(record.get("source_key") or ""), text),
        "text": text,
        "question_text": None,
        "answer_text": None,
        "stance": "unclassified",
        "tags": [],
        "stock_names": [],
    }
    if text is None:
        return base
    question, answer = _split_reply(text)
    base["question_text"], base["answer_text"] = question, answer
    evidence_text = " ".join(value for value in (question, answer, text) if value)
    base["stance"] = _stance(answer)
    base["tags"] = sorted(name for name, pattern in TAG_PATTERNS.items() if pattern.search(evidence_text))
    base["stock_names"] = sorted({name for name in known_names if name and name in evidence_text})
    return base


def summarize_records(records: Iterable[Mapping[str, Any]], *, known_names: Sequence[str] = ()) -> dict[str, Any]:
    """Aggregate only derived counts; raw message text stays out of the summary."""
    items = [distill_record(record, known_names=known_names) for record in records]
    tag_counts = Counter(tag for item in items for tag in item["tags"])
    stock_counts = Counter(name for item in items for name in item["stock_names"])
    return {
        "records": len(items),
        "parseable_text_records": sum(item["content_status"] == "parseable_text" for item in items),
        "content_status": dict(Counter(item["content_status"] for item in items)),
        "sources": dict(Counter(str(item["source_key"]) for item in items)),
        "message_types": dict(Counter(str(item["message_type"]) for item in items)),
        "roles": dict(Counter(item["role"] for item in items)),
        "stances": dict(Counter(item["stance"] for item in items)),
        "tags": dict(tag_counts),
        "stock_mentions": dict(stock_counts),
        "available_at_semantics": "created_at_fallback_source_create_time",
        "live_effect": "none",
    }


__all__ = ["TAG_PATTERNS", "distill_record", "summarize_records"]
