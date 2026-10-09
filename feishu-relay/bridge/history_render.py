"""Render archived LarkAgentX messages into an AI-ready transcript.

The bridge already archives every observed message of every type with the
upstream decoder's readable ``content`` summary plus a structured
``content_data`` (``history_archive``).  This module turns a span of those
rows into clean, flat text a model (or a human) can read — the input the
"learn what this group discussed" analysis step needs.

Most types already carry a good ``content`` summary, so rendering is mostly
formatting.  The exception is CARD/INTERACTIVE: the summary is just "[卡片]"
and the real text lives in ``content_data.richtext`` (LarkAgentX's internal
Card 2.0 node dictionary), so this walks that structure, dropping the
client-upgrade banner and anti-scrape decoy nodes the same way the adapter's
card reader does.

Pure and dependency-free: no larkx, no network. Name resolution is optional
(WS messages carry only ``from_id``); callers may pass a ``name_map``.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

# Mirror of adapter/card-content.mjs isCardUnavailableNotice: the fixed
# client-upgrade banner is transport noise, never content.  Tolerate a leading
# protobuf length-marker digit (observed "5Upgrade...") after control cleanup.
_BANNER_RES = (
    re.compile(r"^(?:\d+\s*)?upgrade to the latest app version to view the content$", re.IGNORECASE),
    re.compile(r"^(?:\d+\s*)?请升级至最新版本客户端，?以查看内容$"),
)
_CONTROL_RE = re.compile(r"[\u0000-\u0008\u000b-\u001f\u007f-\u009f�]")
# The private Feishu image CDN URL must never reach a relay or a model prompt.
_LARK_CDN_RE = re.compile(r"https?://s1-imfile\.feishucdn\.com/\S+", re.IGNORECASE)
# A node pushed out of view with a large negative margin is an anti-scrape
# decoy (observed margin "0px 0px -24px -99px"); real layout never hides a line.
_DECOY_MARGIN_PX = -12


def clean_text(value: Any) -> str:
    text = _CONTROL_RE.sub("", str(value or ""))
    text = _LARK_CDN_RE.sub("", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def is_card_unavailable_notice(value: Any) -> bool:
    normalized = clean_text(value)
    return any(pattern.match(normalized) for pattern in _BANNER_RES)


def _decoy_hidden(node: dict[str, Any]) -> bool:
    margin = (node.get("style") or {}).get("margin")
    if not isinstance(margin, str):
        return False
    return any(float(part) <= _DECOY_MARGIN_PX for part in re.findall(r"-?\d+(?:\.\d+)?(?=px\b)", margin))


def render_card_richtext(richtext: dict[str, Any]) -> str:
    """Collect visible text from a Card 2.0 internal node dictionary, in order."""
    if not isinstance(richtext, dict):
        return ""
    dictionary = (richtext.get("elements") or {}).get("dictionary") or {}
    if not isinstance(dictionary, dict):
        return ""
    root_ids = richtext.get("elementIds") or list(dictionary.keys())
    chunks: list[str] = []
    seen: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in seen:  # guard against malformed cycles
            return
        seen.add(node_id)
        node = dictionary.get(node_id)
        if not isinstance(node, dict) or _decoy_hidden(node):
            return
        if node.get("tag") == 1:  # text leaf
            text = clean_text(node.get("property"))
            if text and not is_card_unavailable_notice(text) and text not in chunks:
                chunks.append(text)
        for child_id in node.get("childIds") or []:
            visit(str(child_id))

    for root_id in root_ids:
        visit(str(root_id))
    return "\n".join(chunks)


def _card_image_count(richtext: dict[str, Any]) -> int:
    dictionary = (richtext.get("elements") or {}).get("dictionary") or {}
    if not isinstance(dictionary, dict):
        return 0
    return sum(1 for node in dictionary.values() if isinstance(node, dict) and node.get("tag") == 2)


def _image_ids(payload: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    one = payload.get("_larkagentx_image") or {}
    if isinstance(one, dict) and one.get("image_id"):
        ids.append(str(one["image_id"]))
    for item in payload.get("_larkagentx_images") or []:
        if isinstance(item, dict) and item.get("image_id") and str(item["image_id"]) not in ids:
            ids.append(str(item["image_id"]))
    return ids


def message_text(payload: dict[str, Any]) -> str:
    """Type-aware readable text for one archived message."""
    msg_type = str(payload.get("msg_type_name") or payload.get("msg_type") or "").upper()
    if msg_type in {"CARD", "INTERACTIVE"}:
        richtext = (payload.get("content_data") or {}).get("richtext") or {}
        text = render_card_richtext(richtext)
        if text:
            return text
        images = _card_image_count(richtext) or len(_image_ids(payload))
        return "[图片]" if images else clean_text(payload.get("content"))
    if msg_type == "IMAGE":
        return "[图片]"
    return clean_text(payload.get("content"))


def render_message(payload: dict[str, Any], *, name_map: dict[str, str] | None = None) -> dict[str, Any]:
    from_id = str(payload.get("from_id") or "")
    sender = (name_map or {}).get(from_id) or from_id or "unknown"
    try:
        epoch = float(payload.get("create_time") or 0)
    except (TypeError, ValueError):
        epoch = 0.0
    return {
        "epoch": epoch,
        "sender": sender,
        "from_id": from_id,
        "type": str(payload.get("msg_type_name") or payload.get("msg_type") or "").upper(),
        "text": message_text(payload),
        "image_ids": _image_ids(payload),
    }


def render_transcript(
    rows: Iterable[dict[str, Any]],
    *,
    name_map: dict[str, str] | None = None,
    drop_system: bool = True,
    include_date: bool = True,
    tz_offset_hours: int = 8,
) -> str:
    """Flatten archived payloads into a chronological, speaker-labelled transcript."""
    tz = timezone(timedelta(hours=tz_offset_hours))
    lines: list[str] = []
    last_day: str | None = None
    rendered = [render_message(_payload_of(row), name_map=name_map) for row in rows]
    rendered.sort(key=lambda item: item["epoch"])
    for item in rendered:
        if drop_system and item["type"] == "SYSTEM":
            continue
        if not item["text"]:
            continue
        stamp = ""
        if item["epoch"] > 0:
            moment = datetime.fromtimestamp(item["epoch"], tz)
            if include_date:
                day = moment.strftime("%Y-%m-%d")
                if day != last_day:
                    lines.append(f"\n== {day} ==")
                    last_day = day
            stamp = moment.strftime("%H:%M") + " "
        text = item["text"].replace("\n", "\n    ")
        lines.append(f"{stamp}{item['sender']}: {text}")
    return "\n".join(lines).strip()


def _payload_of(row: dict[str, Any]) -> dict[str, Any]:
    """Accept either a raw payload dict or an export row wrapping ``payload``."""
    if isinstance(row, dict) and isinstance(row.get("payload"), dict):
        return row["payload"]
    return row
