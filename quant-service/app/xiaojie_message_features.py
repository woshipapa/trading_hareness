"""Point-in-time research features from the 小杰 group messages.

The relay ledger lives on the Feishu relay edge, in that host's own
PostgreSQL, which the peer cannot reach.  ``scripts/sync-xiaojie-message-
features.sh`` reads new rows there (read-only, sender fields stripped) and
pipes them to ``python -m app.xiaojie_message_features ingest``, which runs
the deterministic extractor (``xiaojie_message_distillation``) and stores one
raw observation per message and stock:

* ``available_at`` is when the ledger first received the message - the only
  time a strategy may treat it as known;
* ``stated_at`` is the author time written at the end of a reply, kept for
  review but never used as availability;
* role, stance, rule tags and resolved stock codes; text is kept only for
  the instructor's replies, statements and night reports - an excerpt, plus
  the passage around each stock it names - never for participants.

These are research features.  Nothing here feeds a live threshold or a
decision; the only live use is showing the instructor's latest stance next to
an alert that already fired.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from psycopg.types.json import Json

from .xiaojie_message_distillation import distill_record

PROVIDER_KEY = "feishu_group_relay"
CAPABILITY = "xiaojie_message_feature"
EXTRACTOR_VERSION = "xiaojie-message-distillation-v1"
#: Messages that name no stock are market commentary, stored under this key.
MARKET_SYMBOL = "market:xiaojie"
#: Two-character names match ordinary words ("科技" is not a stock), so only
#: names of three characters or more are resolved.
MIN_NAME_LENGTH = 3
ANSWER_EXCERPT_CHARS = 600
#: Characters kept either side of a stock name in a long report.
MENTION_WINDOW_CHARS = 60
#: Roles whose text is the instructor's own and may be kept.
ROLE_LABELS: dict[str, str] = {
    "instructor_reply": "讲师回复",
    "instructor_statement": "讲师",
    "night_report_or_review": "夜报/复盘",
}

#: Chinese labels for the extractor's stances, for display next to an alert.
STANCE_LABELS: dict[str, str] = {
    "confirmed": "确认",
    "confirmed_conditional": "带条件确认",
    "support_or_wait": "支撑/等待",
    "risk_or_exit": "风险/退出",
    "not_confirmed_or_risk": "未确认/风险",
    "position_guidance": "仓位指导",
    "observation": "观察",
    "unclassified": "未分类",
}

_CHINA = ZoneInfo("Asia/Shanghai")
_STATED_AT = re.compile(r"——\s*(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?)")


def stated_at(text: str | None) -> str | None:
    """The author time a reply ends with (``—— 2026-09-17 11:08:59``), as UTC."""
    match = _STATED_AT.search(text or "")
    if not match:
        return None
    try:
        local = datetime.fromisoformat(match.group(1).replace(" ", "T"))
    except ValueError:
        return None
    return local.replace(tzinfo=_CHINA).astimezone(timezone.utc).isoformat()


def name_index(connection: Any) -> dict[str, str]:
    """Instrument short name to symbol, for names long enough to be unambiguous."""
    rows = connection.execute(
        "SELECT symbol, name FROM quant.instruments WHERE name IS NOT NULL AND length(name) >= %s",
        (MIN_NAME_LENGTH,),
    ).fetchall()
    index: dict[str, str] = {}
    for row in rows:
        name = str(row["name"]).replace(" ", "")
        if len(name) >= MIN_NAME_LENGTH:
            index.setdefault(name, str(row["symbol"]))
    return index


#: A reply's header names the participant who asked; no handle is kept.
_HANDLE = re.compile(r"@[^\s：:，,]+")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", _HANDLE.sub("@", text)).strip()


def _mention(text: str, name: str) -> str | None:
    at = text.find(name)
    if at < 0:
        return None
    return _clean(text[max(0, at - MENTION_WINDOW_CHARS):at + len(name) + MENTION_WINDOW_CHARS])


def feature_payload(record: Mapping[str, Any], names: Mapping[str, str]) -> dict[str, Any] | None:
    """One message's features, or None when it carries no readable text."""
    item = distill_record(record, known_names=tuple(names))
    if item["content_status"] == "unavailable_non_text" or not item["source_message_id"] or not item["available_at"]:
        return None
    keep_text = item["role"] in ROLE_LABELS
    # A reply keeps only the instructor's answer: the question above the
    # divider is the participant's, with their handle in it.
    own_text = (item["answer_text"] if item["role"] == "instructor_reply" else item["text"]) or ""
    excerpt = _clean(own_text) if keep_text and own_text else None
    mentions: dict[str, str] = {}
    if keep_text:
        for name in item["stock_names"]:
            passage = _mention(own_text, name) if name in names else None
            if passage:
                mentions[names[name]] = passage
    return {
        "source_key": item["source_key"],
        "source_message_id": item["source_message_id"],
        "available_at": item["available_at"],
        "stated_at": stated_at(item["text"]),
        "message_type": item["message_type"],
        "content_status": item["content_status"],
        "role": item["role"],
        "stance": item["stance"],
        "tags": item["tags"],
        "stock_names": item["stock_names"],
        "symbols": sorted({names[name] for name in item["stock_names"] if name in names}),
        "excerpt": excerpt[:ANSWER_EXCERPT_CHARS] if excerpt else None,
        "mentions": mentions,
        "extractor_version": EXTRACTOR_VERSION,
        "live_effect": "none",
    }


def store(connection: Any, payloads: Iterable[Mapping[str, Any]]) -> int:
    """Insert one observation per message and stock; a message seen before is skipped."""
    stored = 0
    for payload in payloads:
        effective_at = payload.get("stated_at") or payload["available_at"]
        for symbol in payload["symbols"] or [MARKET_SYMBOL]:
            key = f"{payload['source_message_id']}|{symbol}|{EXTRACTOR_VERSION}"
            row = connection.execute(
                """INSERT INTO quant.raw_market_observations(
                        provider_key,capability,market,symbol,effective_at,available_at,payload_sha256,normalized,payload)
                   VALUES(%s,%s,'cn',%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(provider_key,capability,market,symbol,effective_at,payload_sha256) DO NOTHING
                   RETURNING observation_id""",
                (PROVIDER_KEY, CAPABILITY, symbol, effective_at, payload["available_at"],
                 hashlib.sha256(key.encode()).hexdigest(), Json(dict(payload)), Json(dict(payload))),
            ).fetchone()
            stored += row is not None
    return stored


def ingest(connection: Any, records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    names = name_index(connection)
    received, skipped, payloads = 0, 0, []
    for record in records:
        received += 1
        payload = feature_payload(record, names)
        if payload is None:
            skipped += 1
        else:
            payloads.append(payload)
    return {"received": received, "non_text": skipped, "messages": len(payloads),
            "with_stock": sum(1 for payload in payloads if payload["symbols"]),
            "stored": store(connection, payloads), "names_indexed": len(names)}


def cursor(connection: Any) -> datetime | None:
    """The newest ledger receive time already stored."""
    row = connection.execute(
        "SELECT max(available_at) AS latest FROM quant.raw_market_observations WHERE provider_key=%s AND capability=%s",
        (PROVIDER_KEY, CAPABILITY),
    ).fetchone()
    return row["latest"] if row else None


def features(connection: Any, *, as_of: datetime, symbol: str | None = None, since: datetime | None = None,
             instructor_only: bool = False, limit: int = 100) -> list[dict[str, Any]]:
    """Features known at ``as_of``: the availability rule is applied here, not by the caller."""
    since = since or as_of - timedelta(days=30)
    rows = connection.execute(
        """SELECT symbol, effective_at, available_at, normalized
             FROM quant.raw_market_observations
            WHERE provider_key=%s AND capability=%s AND available_at <= %s AND available_at >= %s
              AND (%s::text IS NULL OR symbol = %s)
              AND (NOT %s OR normalized->>'role' = ANY(%s))
            ORDER BY available_at DESC LIMIT %s""",
        (PROVIDER_KEY, CAPABILITY, as_of, since, symbol, symbol, instructor_only, list(ROLE_LABELS),
         max(1, min(int(limit), 500))),
    ).fetchall()
    return [{"symbol": row["symbol"], **dict(row["normalized"])} for row in rows]


def latest_instructor_line(items: list[Mapping[str, Any]], symbol: str) -> str | None:
    """One line for an alert: the instructor's latest words on this stock."""
    for item in items:
        role = str(item.get("role") or "")
        if role not in ROLE_LABELS:
            continue
        try:
            local = datetime.fromisoformat(str(item.get("available_at"))).astimezone(_CHINA).strftime("%m-%d %H:%M")
        except (TypeError, ValueError):
            local = "?"
        label = ROLE_LABELS[role]
        if item.get("stance") not in (None, "unclassified"):
            label += f"「{STANCE_LABELS.get(str(item['stance']), str(item['stance']))}」"
        passage = (item.get("mentions") or {}).get(symbol) or item.get("excerpt") or ""
        passage = re.sub(r"\s+", " ", str(passage)).strip()
        return f"小杰群聊 {local} {label}：{passage[:80]}" if passage else f"小杰群聊 {local} {label}"
    return None


def main() -> None:  # pragma: no cover - operational entry point
    from . import main as service

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("ingest", "cursor"))
    args = parser.parse_args()
    if args.action == "cursor":
        with service.db.transaction() as connection:
            latest = cursor(connection)
        print(latest.isoformat() if latest else "none")
        return
    records = [json.loads(line) for line in sys.stdin if line.strip()]
    with service.db.transaction() as connection:
        print(json.dumps(ingest(connection, records), ensure_ascii=False))


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = [
    "CAPABILITY", "EXTRACTOR_VERSION", "MARKET_SYMBOL", "PROVIDER_KEY", "ROLE_LABELS", "STANCE_LABELS",
    "cursor", "feature_payload", "features", "ingest", "latest_instructor_line", "name_index",
    "stated_at", "store",
]
