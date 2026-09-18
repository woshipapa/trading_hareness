"""Normalizers for the Fuyao/THS routes beyond the limit pools.

Rankings (hot list, skyrocket list, valuation, index quotes) are time series
and become timed raw observations; discrete facts (anomaly explanations, the
dragon-tiger list, the auction short-term benchmark) become market events.
Everything stays research evidence.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any, Awaitable, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from ..http import ashare_symbol, number


PROVIDER_KEY = "fuyao_ths"
CN_TZ = ZoneInfo("Asia/Shanghai")
#: Provider limits measured 2026-09-18: pools page at 50 by default (size
#: 1..200); ``thscodes`` takes at most 100 codes and one delisted or
#: non-equity code fails the whole batch ("Unknown thscode: 000003.SZ").
POOL_PAGE_SIZE = 200
MAX_POOL_PAGES = 10
MAX_CODES_PER_REQUEST = 100
MAX_BAD_CODE_RETRIES = 8
_BAD_CODE = re.compile(r"(?:Unknown thscode|Not an A-share stock):\s*([0-9A-Z.]+)")
FuyaoFetch = Callable[[str, dict[str, Any]], Awaitable[Mapping[str, Any]]]


def _items(data: Mapping[str, Any] | None, key: str = "item") -> list[dict[str, Any]]:
    value = (data or {}).get(key)
    return [dict(row) for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


def _upstream_time(data: Mapping[str, Any] | None, observed_at: datetime) -> datetime:
    try:
        stamp = float((data or {}).get("timestamp"))
    except (TypeError, ValueError):
        return observed_at
    upstream = datetime.fromtimestamp(stamp / 1000, tz=observed_at.tzinfo or CN_TZ)
    return min(upstream, observed_at)


def rank_observations(capability: str, data: Mapping[str, Any] | None, observed_at: datetime) -> list[dict[str, Any]]:
    """Hot / skyrocket list rows: one timed observation per ranked stock."""
    effective = _upstream_time(data, observed_at).isoformat()
    rows = []
    for item in _items(data):
        symbol = ashare_symbol(item.get("thscode"))
        rank = number(item.get("rank"))
        if symbol is None or rank is None:
            continue
        rows.append({
            "ts_code": symbol, "rank": int(rank), "heat": number(item.get("heat")),
            "rank_change": int(number(item.get("rank_change")) or 0), "rank_trend": item.get("rank_trend"),
            "name": item.get("name"), "capability": capability,
            "effective_at": effective, "available_at": observed_at.isoformat(),
        })
    return rows


def hot_history_observations(data: Mapping[str, Any] | None, observed_at: datetime) -> list[dict[str, Any]]:
    """The settled daily hot list for ``data['date']`` (post-close archive)."""
    try:
        day = date.fromisoformat(str((data or {}).get("date") or ""))
    except ValueError:
        return []
    effective = min(datetime.combine(day, time(15, 0), CN_TZ), observed_at).isoformat()
    rows = []
    for item in _items(data):
        symbol = ashare_symbol(item.get("thscode"))
        rank = number(item.get("rank"))
        if symbol and rank is not None:
            rows.append({"ts_code": symbol, "rank": int(rank), "name": item.get("name"), "trade_date": day.isoformat(),
                         "effective_at": effective, "available_at": observed_at.isoformat()})
    return rows


def anomaly_events(data: Mapping[str, Any] | None, observed_at: datetime) -> list[dict[str, Any]]:
    """个股异动 explanations; one identity per stock, day and anomaly tag."""
    day = observed_at.astimezone(CN_TZ).date().isoformat()
    events = []
    for item in _items(data):
        symbol = ashare_symbol(item.get("thscode"))
        if symbol is None:
            continue
        tag = str(item.get("tag_name") or "异动").strip()
        events.append({
            "ts_code": symbol, "event_type": "stock_anomaly",
            "published_at": _upstream_time(data, observed_at).isoformat(),
            "title": f"个股异动：{item.get('stock_name') or symbol} {tag}", "url": None,
            "event_identity_key": f"{PROVIDER_KEY}:stock_anomaly:{symbol}:{day}:{tag}",
            "raw": {"capability": "a_share_anomaly_analysis_list", "tag_name": tag,
                    "keywords": item.get("keyword_list") or [], "analysis": item.get("analysis_content"),
                    "ai_generated": True, **{key: item.get(key) for key in ("thscode", "stock_name")}},
        })
    return events


def dragon_tiger_events(data: Mapping[str, Any] | None, observed_at: datetime) -> list[dict[str, Any]]:
    """THS dragon-tiger stock rows (net buy, hot-money net) for ``trade_date``."""
    trade_date = str((data or {}).get("trade_date") or "")[:10]
    if not trade_date:
        return []
    events = []
    for item in _items(data, "stock_items"):
        symbol = ashare_symbol(item.get("thscode"))
        if symbol is None:
            continue
        net = number(item.get("net_value"))
        events.append({
            "ts_code": symbol, "event_type": "lhb_ths", "published_at": observed_at.isoformat(),
            "title": f"龙虎榜（同花顺）：{item.get('name') or symbol} 净买{(net or 0) / 1e4:,.0f}万",
            "url": None, "event_identity_key": f"{PROVIDER_KEY}:lhb_ths:{symbol}:{trade_date}",
            "raw": {"capability": "a_share_dragon_tiger_list", "trade_date": trade_date,
                    "board_type": (data or {}).get("board_type"),
                    "concepts": [concept.get("name") for concept in item.get("concept_list") or [] if isinstance(concept, Mapping)],
                    **{key: item.get(key) for key in (
                        "name", "change", "net_value", "net_rate", "buy_value", "sell_value", "hot_rank",
                        "range_days", "hot_money_net_value", "limit_reason")}},
        })
    return events


def dragon_tiger_hot_money_observations(data: Mapping[str, Any] | None, observed_at: datetime) -> list[dict[str, Any]]:
    """Hot-money seat rows are not per-stock, so they stay raw observations."""
    trade_date = str((data or {}).get("trade_date") or "")[:10]
    if not trade_date:
        return []
    effective = min(datetime.combine(date.fromisoformat(trade_date), time(15, 0), CN_TZ), observed_at).isoformat()
    return [{"ts_code": ashare_symbol(item.get("thscode")), "trade_date": trade_date, **item,
             "effective_at": effective, "available_at": observed_at.isoformat()}
            for item in _items(data, "hot_money_items")]


def auction_benchmark_events(data: Mapping[str, Any] | None, observed_at: datetime) -> list[dict[str, Any]]:
    """THS 竞价短线基准 list published after the 09:25 match."""
    day = str((data or {}).get("date") or observed_at.astimezone(CN_TZ).date().isoformat())[:10]
    events = []
    for item in _items(data):
        symbol = ashare_symbol(item.get("thscode"))
        if symbol is None:
            continue
        events.append({
            "ts_code": symbol, "event_type": "auction_short_term_benchmark",
            "published_at": observed_at.isoformat(),
            "title": f"竞价短线基准：{item.get('name') or symbol} {number(item.get('auction_pct')) or 0:+.2f}%",
            "url": None, "event_identity_key": f"{PROVIDER_KEY}:auction_short_term_benchmark:{symbol}:{day}",
            "raw": {"capability": "a_share_auction_short_term_benchmark", "date": day, **item},
        })
    return events


def valuation_observations(data: Mapping[str, Any] | None, observed_at: datetime) -> list[dict[str, Any]]:
    effective = _upstream_time(data, observed_at).isoformat()
    rows = []
    for item in _items(data):
        symbol = ashare_symbol(item.get("thscode"))
        if symbol:
            rows.append({"ts_code": symbol, **{key: number(item.get(key)) for key in (
                "pe_ttm", "pe_mrq", "pb_mrq", "ps_ttm", "pcf_ttm")},
                "effective_at": effective, "available_at": observed_at.isoformat()})
    return rows


def index_quote_observations(data: Mapping[str, Any] | None, observed_at: datetime,
                             names: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """THS concept/industry index quotes; ``ts_code`` is empty (not a stock)."""
    effective = _upstream_time(data, observed_at).isoformat()
    rows = []
    for item in _items(data):
        code = str(item.get("thscode") or "").upper()
        if not code:
            continue
        rows.append({"ts_code": None, "index_code": code, "index_name": (names or {}).get(code),
                     **{key: number(item.get(key)) for key in (
                         "last_price", "prev_price", "open_price", "high_price", "low_price",
                         "price_change_ratio_pct", "volume", "turnover")},
                     "effective_at": effective, "available_at": observed_at.isoformat()})
    return rows


def index_catalog(data: Mapping[str, Any] | None) -> dict[str, str]:
    return {str(item.get("thscode")).upper(): str(item.get("name") or "") for item in _items(data) if item.get("thscode")}


def constituent_symbols(data: Mapping[str, Any] | None) -> list[str]:
    return sorted({symbol for symbol in (ashare_symbol(item.get("thscode")) for item in _items(data)) if symbol})


async def fetch_all_pool_pages(
    fetch: FuyaoFetch, capability: str,
) -> dict[str, Any]:
    """Walk a paginated pool and return one merged ``{"item": [...]}``."""
    items: list[dict[str, Any]] = []
    first: Mapping[str, Any] = {}
    for page in range(1, MAX_POOL_PAGES + 1):
        data = await fetch(capability, {"page": page, "size": POOL_PAGE_SIZE})
        first = first or data
        page_items = _items(data)
        items.extend(page_items)
        pagination = data.get("pagination") if isinstance(data, Mapping) else None
        pages = int((pagination or {}).get("pages") or 1) if isinstance(pagination, Mapping) else 1
        if page >= pages or not page_items:
            break
    return {**dict(first), "item": items}


def equity_codes(symbols: Sequence[str]) -> list[str]:
    """A-share equities only, de-duplicated and ordered (indices dropped)."""
    return sorted({symbol for symbol in (ashare_symbol(item) for item in symbols) if symbol})


async def fetch_code_batches(
    fetch: FuyaoFetch,
    capability: str,
    symbols: Sequence[str],
    extra: Mapping[str, Any] | None = None,
) -> tuple[list[tuple[list[str], Mapping[str, Any]]], list[str], list[str]]:
    """Fetch ``symbols`` 100 at a time, dropping codes the provider rejects.

    Returns ``(batches, dropped_codes, failures)``.  A batch that fails for a
    named code is retried without it; any other failure is recorded and the
    remaining batches continue.
    """
    batches: list[tuple[list[str], Mapping[str, Any]]] = []
    dropped: list[str] = []
    failures: list[str] = []
    codes = equity_codes(symbols)
    for offset in range(0, len(codes), MAX_CODES_PER_REQUEST):
        chunk = codes[offset:offset + MAX_CODES_PER_REQUEST]
        for _attempt in range(MAX_BAD_CODE_RETRIES + 1):
            if not chunk:
                break
            try:
                data = await fetch(capability, {**dict(extra or {}), "thscodes": ",".join(chunk)})
            except Exception as error:  # noqa: BLE001 - the provider names the bad code in its message
                bad = _BAD_CODE.search(str(error))
                if bad and bad.group(1).upper() in chunk:
                    chunk.remove(bad.group(1).upper())
                    dropped.append(bad.group(1).upper())
                    continue
                failures.append(str(error)[:180])
                break
            batches.append((chunk, data))
            break
    return batches, dropped, failures


__all__ = [
    "MAX_CODES_PER_REQUEST", "equity_codes", "fetch_all_pool_pages", "fetch_code_batches",
    "anomaly_events", "auction_benchmark_events", "constituent_symbols", "dragon_tiger_events",
    "dragon_tiger_hot_money_observations",
    "hot_history_observations", "index_catalog", "index_quote_observations", "rank_observations",
    "valuation_observations",
]
