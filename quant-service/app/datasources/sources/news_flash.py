"""7x24 flash-news adapters: CLS telegraph, Jin10, Eastmoney and THS.

Each adapter returns :class:`dict` flashes in one normalized shape::

    {provider, flash_id, published_at, title, content, importance,
     symbols, tags, url, upstream_site}

``importance`` is ``critical`` / ``important`` / ``normal`` from the vendor's
own flag (CLS level A/B, Jin10 ``important``, a red THS/Eastmoney headline).
``symbols`` holds only A-share equities the vendor itself attached; nothing is
inferred from the text.  Paid/VIP items with no public content are dropped.

The CLS ``sign`` below is the public web client's request checksum
(md5 of sha1 of the query string), not a credential.
"""

from __future__ import annotations

import hashlib
import html
import re
import time as time_module
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from ..http import ashare_symbol, request_json


CN_TZ = ZoneInfo("Asia/Shanghai")
MAX_CONTENT_CHARS = 4000
_TAGS = re.compile(r"<[^>]+>")

FLASH_PROVIDERS: dict[str, dict[str, str]] = {
    "cls_telegraph": {"label": "财联社电报", "upstream_site": "www.cls.cn"},
    "jin10_flash": {"label": "金十快讯", "upstream_site": "flash-api.jin10.com"},
    "eastmoney_flash": {"label": "东方财富 7x24 快讯", "upstream_site": "np-weblist.eastmoney.com"},
    "ths_flash": {"label": "同花顺快讯", "upstream_site": "news.10jqka.com.cn"},
}


def _text(value: Any) -> str:
    return html.unescape(_TAGS.sub("", str(value or ""))).strip()


def _epoch(value: Any) -> datetime | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds > 1e12:  # milliseconds
        seconds /= 1000
    return datetime.fromtimestamp(seconds, tz=timezone.utc) if seconds > 0 else None


def _local(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=CN_TZ)).astimezone(timezone.utc)


def _flash(provider: str, flash_id: Any, published_at: datetime | None, title: str, content: str,
           importance: str, symbols: list[str], tags: list[str], url: str | None) -> dict[str, Any] | None:
    identity = str(flash_id or "").strip()
    if not identity or published_at is None or not (title or content):
        return None
    if not title:
        match = re.match(r"^【([^】]{2,80})】", content)
        title = match.group(1) if match else content[:60]
    return {
        "provider": provider, "flash_id": identity, "published_at": published_at.isoformat(),
        "title": title[:300], "content": content[:MAX_CONTENT_CHARS], "importance": importance,
        "symbols": sorted(set(symbols)), "tags": [tag for tag in tags if tag][:12], "url": url,
        "upstream_site": FLASH_PROVIDERS[provider]["upstream_site"],
    }


# -- CLS ---------------------------------------------------------------------

def cls_sign(params: Mapping[str, Any]) -> str:
    return hashlib.md5(hashlib.sha1(urlencode(params).encode()).hexdigest().encode()).hexdigest()


def normalize_cls(payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    data = (payload or {}).get("data")
    items = data.get("roll_data") if isinstance(data, Mapping) else None
    rows = []
    for item in items or []:
        if not isinstance(item, Mapping):
            continue
        level = str(item.get("level") or "C").upper()
        symbols = [symbol for symbol in (
            ashare_symbol(stock.get("StockID")) for stock in item.get("stock_list") or [] if isinstance(stock, Mapping)
        ) if symbol]
        tags = [str(subject.get("subject_name") or "") for subject in item.get("subjects") or [] if isinstance(subject, Mapping)]
        row = _flash(
            "cls_telegraph", item.get("id"), _epoch(item.get("ctime")), _text(item.get("title")),
            _text(item.get("content") or item.get("brief")),
            "critical" if level == "A" else "important" if level == "B" else "normal",
            symbols, tags, str(item.get("shareurl") or "") or None,
        )
        if row:
            rows.append(row)
    return rows


async def fetch_cls(limit: int = 50) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "app": "CailianpressWeb", "category": "", "last_time": int(time_module.time()), "os": "web",
        "refresh_type": "1", "rn": str(max(1, min(limit, 100))), "sv": "8.4.6",
    }
    params["sign"] = cls_sign(params)
    payload = await request_json("GET", "https://www.cls.cn/v1/roll/get_roll_list", params=params)
    if not isinstance(payload, Mapping) or payload.get("errno") not in (0, None):
        raise ValueError("CLS telegraph response is not a success object")
    return normalize_cls(payload)


# -- Jin10 -------------------------------------------------------------------

def normalize_jin10(payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    rows = []
    for item in (payload or {}).get("data") or []:
        if not isinstance(item, Mapping) or item.get("type") not in (0, None):
            continue
        data = item.get("data") if isinstance(item.get("data"), Mapping) else {}
        content = _text(data.get("content"))
        if not content or data.get("vip_title") or data.get("lock"):
            continue  # VIP-only items have no public text
        row = _flash(
            "jin10_flash", item.get("id"), _local(item.get("time")), _text(data.get("title")), content,
            "important" if int(item.get("important") or 0) else "normal", [],
            [str(tag) for tag in item.get("tags") or [] if isinstance(tag, str)], None,
        )
        if row:
            rows.append(row)
    return rows


async def fetch_jin10() -> list[dict[str, Any]]:
    payload = await request_json(
        "GET", "https://flash-api.jin10.com/get_flash_list", params={"channel": "-8200", "vip": "1"},
        # The public web client's fixed app id and version, not a credential.
        headers={"x-app-id": "bVBF4FyRTn5NJF5n", "x-version": "1.0.0",
                 "Origin": "https://www.jin10.com", "Referer": "https://www.jin10.com/"},
    )
    if not isinstance(payload, Mapping):
        raise ValueError("Jin10 flash response is not an object")
    return normalize_jin10(payload)


# -- Eastmoney ---------------------------------------------------------------

def _eastmoney_secid(value: Any) -> str | None:
    market, _, code = str(value or "").partition(".")
    if market not in {"0", "1"}:
        return None
    return ashare_symbol(code, "SH" if market == "1" else None)


def normalize_eastmoney(payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    data = (payload or {}).get("data")
    items = data.get("fastNewsList") if isinstance(data, Mapping) else None
    rows = []
    for item in items or []:
        if not isinstance(item, Mapping):
            continue
        code = str(item.get("code") or "")
        symbols = [symbol for symbol in (_eastmoney_secid(value) for value in item.get("stockList") or []) if symbol]
        row = _flash(
            "eastmoney_flash", code, _local(item.get("showTime")), _text(item.get("title")),
            _text(item.get("summary")), "important" if int(item.get("titleColor") or 0) else "normal",
            [symbol for symbol in symbols if symbol.endswith((".SH", ".SZ", ".BJ"))], [],
            f"https://finance.eastmoney.com/a/{code}.html" if code else None,
        )
        if row:
            rows.append(row)
    return rows


async def fetch_eastmoney(limit: int = 50) -> list[dict[str, Any]]:
    payload = await request_json("GET", "https://np-weblist.eastmoney.com/comm/web/getFastNewsList", params={
        "client": "web", "biz": "web_724", "fastColumn": "102", "sortEnd": "",
        "pageSize": str(max(1, min(limit, 200))), "req_trace": str(int(time_module.time() * 1000)),
    })
    if not isinstance(payload, Mapping):
        raise ValueError("Eastmoney flash response is not an object")
    return normalize_eastmoney(payload)


# -- THS ---------------------------------------------------------------------

#: THS ``stockMarket`` codes for A-share boards; US/HK codes are ignored.
_THS_MARKETS = {"17": "SH", "33": "SZ", "151": "BJ"}


def normalize_ths(payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    data = (payload or {}).get("data")
    items = data.get("list") if isinstance(data, Mapping) else None
    rows = []
    for item in items or []:
        if not isinstance(item, Mapping):
            continue
        symbols = []
        for stock in item.get("stock") or []:
            if isinstance(stock, Mapping) and str(stock.get("stockMarket")) in _THS_MARKETS:
                symbol = ashare_symbol(stock.get("stockCode"), _THS_MARKETS[str(stock.get("stockMarket"))])
                if symbol:
                    symbols.append(symbol)
        row = _flash(
            "ths_flash", item.get("seq") or item.get("id"), _epoch(item.get("rtime") or item.get("ctime")),
            _text(item.get("title")), _text(item.get("digest") or item.get("short")),
            "important" if str(item.get("color") or "") == "2" else "normal", symbols,
            [str(tag.get("name") or "") for tag in item.get("tags") or [] if isinstance(tag, Mapping)],
            str(item.get("url") or "") or None,
        )
        if row:
            rows.append(row)
    return rows


async def fetch_ths(limit: int = 50) -> list[dict[str, Any]]:
    payload = await request_json("GET", "https://news.10jqka.com.cn/tapp/news/push/stock", params={
        "page": "1", "tag": "", "track": "website", "pagesize": str(max(1, min(limit, 100))),
    })
    if not isinstance(payload, Mapping):
        raise ValueError("THS flash response is not an object")
    return normalize_ths(payload)


FETCHERS: dict[str, Callable[[], Awaitable[list[dict[str, Any]]]]] = {
    "cls_telegraph": fetch_cls, "jin10_flash": fetch_jin10,
    "eastmoney_flash": fetch_eastmoney, "ths_flash": fetch_ths,
}


def flash_observation(flash: Mapping[str, Any], observed_at: datetime) -> dict[str, Any]:
    """Timed raw observation: effective at publication, available when seen."""
    published = datetime.fromisoformat(str(flash["published_at"]))
    return {
        **dict(flash), "ts_code": None,
        "effective_at": min(published, observed_at).isoformat(), "available_at": observed_at.isoformat(),
    }


def flash_events(flash: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One ``news_flash`` market event per vendor-attached A-share symbol."""
    events = []
    for symbol in flash.get("symbols") or []:
        events.append({
            "ts_code": symbol, "event_type": "news_flash", "published_at": flash["published_at"],
            "title": f"{FLASH_PROVIDERS[flash['provider']]['label']}：{flash['title']}"[:300],
            "url": flash.get("url"),
            "event_identity_key": f"{flash['provider']}:news_flash:{flash['flash_id']}:{symbol}",
            "raw": {"capability": "news_flash", **{key: flash.get(key) for key in (
                "provider", "flash_id", "importance", "content", "tags", "upstream_site")}},
        })
    return events


__all__ = [
    "FETCHERS", "FLASH_PROVIDERS", "cls_sign", "fetch_cls", "fetch_eastmoney", "fetch_jin10", "fetch_ths",
    "flash_events", "flash_observation", "normalize_cls", "normalize_eastmoney", "normalize_jin10", "normalize_ths",
]
