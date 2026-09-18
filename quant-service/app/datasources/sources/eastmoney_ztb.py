"""Eastmoney ``push2ex`` limit-board topic: six pools plus tape anomalies.

The six pools carry facts the Fuyao pools do not: first/last seal time, seal
fund, open count and the "N days M boards" statistic for the limit-up pool;
yesterday's limit-ups with today's move; the strong, sub-new and limit-down
pools.  Eastmoney serves only a recent window, so the pools are archived once
per session after the close.

``getAllStockChanges`` is the 盘口异动 tape (rocket launch, big buy, seal/open
limit ...) and ``getAllBKChanges`` its per-board aggregate.

Everything here is research evidence (``decision_eligible=false``).  The
module fetches and normalizes only; cadence and persistence live elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping

from ..http import ashare_symbol, number, request_json


PROVIDER_KEY = "eastmoney_ztb"
UPSTREAM_SITE = "push2ex.eastmoney.com"
_UT = "7eea3edcaed734bea9cbfc24409ed989"
_BASE = "https://push2ex.eastmoney.com"


@dataclass(frozen=True)
class PoolSpec:
    key: str
    path: str
    sort: str
    event_type: str
    label: str


POOLS: dict[str, PoolSpec] = {
    spec.key: spec for spec in (
        PoolSpec("limit_up", "getTopicZTPool", "fbt:asc", "limit_up_pool", "涨停股池"),
        PoolSpec("previous_limit_up", "getYesterdayZTPool", "zs:desc", "previous_limit_pool", "昨日涨停股池"),
        PoolSpec("strong", "getTopicQSPool", "zdp:desc", "strong_pool", "强势股池"),
        PoolSpec("sub_new", "getTopicCXPooll", "ods:asc", "sub_new_limit_pool", "次新股池"),
        PoolSpec("broken", "getTopicZBPool", "fbt:asc", "limit_open_pool", "炸板股池"),
        PoolSpec("limit_down", "getTopicDTPool", "fund:asc", "limit_down_pool", "跌停股池"),
    )
}

#: Pools published as ``market_events``.  The Fuyao collector already writes
#: limit-up, broken and limit-down pool events every minute; readers take the
#: latest row per symbol, so mixing a second vendor's body shape into those
#: event types would change what they parse.  Eastmoney is the only source of
#: these three, whose event types the review and context readers already use.
MARKET_EVENT_POOLS: frozenset[str] = frozenset({"previous_limit_up", "strong", "sub_new"})

#: ``cc`` of the strong pool: why the name was admitted.
STRONG_POOL_REASONS: dict[int, str] = {1: "60日新高", 2: "近期多次涨停", 3: "60日新高且近期多次涨停"}

STOCK_CHANGE_TYPES: dict[int, str] = {
    8201: "火箭发射", 8202: "快速反弹", 8193: "大笔买入", 4: "封涨停板", 32: "打开跌停板",
    64: "有大买盘", 8207: "竞价上涨", 8209: "高开5日线", 8211: "向上缺口", 8213: "60日新高",
    8215: "60日大幅上涨", 8204: "加速下跌", 8203: "高台跳水", 8194: "大笔卖出", 8: "封跌停板",
    16: "打开涨停板", 128: "有大卖盘", 8208: "竞价下跌", 8210: "低开5日线", 8212: "向下缺口",
    8214: "60日新低", 8216: "60日大幅下跌",
}
BEARISH_CHANGE_TYPES: frozenset[int] = frozenset({8204, 8203, 8194, 8, 16, 128, 8208, 8210, 8212, 8214, 8216})
#: Discrete, low-volume anomalies worth a per-stock event row.  The bulk
#: "有大买盘/有大卖盘" and 60-day statistics are kept as a daily summary.
EVENT_CHANGE_TYPES: tuple[int, ...] = (8201, 8202, 8193, 8194, 4, 16, 8, 32, 8203, 8204)


def _clock(value: Any) -> str | None:
    """``92500`` -> ``09:25:00``; zero/blank means the field is absent."""
    parsed = number(value)
    if parsed is None or parsed <= 0:
        return None
    text = str(int(parsed)).zfill(6)
    return f"{text[:2]}:{text[2:4]}:{text[4:]}"


def _price(value: Any) -> float | None:
    """Pool prices arrive as integer thousandths; a 1e9 sentinel means none."""
    parsed = number(value)
    if parsed is None or parsed <= 0 or parsed >= 1_000_000_000:
        return None
    return round(parsed / 1000, 3)


def _symbol(item: Mapping[str, Any]) -> str | None:
    # ``m`` is 1 for Shanghai and 0 for both Shenzhen and Beijing, so the code
    # prefix decides; ``m`` can only veto a Shanghai/other mismatch.
    symbol = ashare_symbol(item.get("c"))
    if symbol is None:
        return None
    market = item.get("m")
    if market == 1 and not symbol.endswith(".SH"):
        return None
    if market == 0 and symbol.endswith(".SH"):
        return None
    return symbol


def normalize_pool_item(pool: str, item: Mapping[str, Any]) -> dict[str, Any] | None:
    """Map one pool item to named fields; unknown pools raise ``KeyError``."""
    spec = POOLS[pool]
    symbol = _symbol(item)
    if symbol is None:
        return None
    stat = item.get("zttj") if isinstance(item.get("zttj"), Mapping) else {}
    stat_days, stat_boards = number(stat.get("days")), number(stat.get("ct"))
    row: dict[str, Any] = {
        "symbol": symbol, "name": str(item.get("n") or "").strip() or None, "pool": pool,
        "event_type": spec.event_type, "price": _price(item.get("p")),
        "pct_change": number(item.get("zdp")), "amount": number(item.get("amount")),
        "float_market_cap": number(item.get("ltsz")), "total_market_cap": number(item.get("tshare")),
        "turnover_rate": number(item.get("hs")), "industry": str(item.get("hybk") or "").strip() or None,
        "limit_stat_days": int(stat_days) if stat_days is not None else None,
        "limit_stat_boards": int(stat_boards) if stat_boards is not None else None,
    }
    if stat_days is not None and stat_boards is not None and stat_boards > 0:
        row["limit_stat_text"] = f"{int(stat_days)}天{int(stat_boards)}板"
    if pool == "limit_up":
        row.update({
            "board_count": int(number(item.get("lbc")) or 0) or None,
            "first_seal_time": _clock(item.get("fbt")), "last_seal_time": _clock(item.get("lbt")),
            "seal_fund": number(item.get("fund")), "open_times": int(number(item.get("zbc")) or 0),
        })
    elif pool == "previous_limit_up":
        row.update({
            "limit_price": _price(item.get("ztp")), "amplitude": number(item.get("zf")),
            "speed_pct": number(item.get("zs")),
            "previous_first_seal_time": _clock(item.get("yfbt")),
            "previous_board_count": int(number(item.get("ylbc")) or 0) or None,
        })
    elif pool == "strong":
        reason = int(number(item.get("cc")) or 0)
        row.update({
            "limit_price": _price(item.get("ztp")), "is_new_high": bool(number(item.get("nh"))),
            "reason_code": reason or None, "reason": STRONG_POOL_REASONS.get(reason),
            "volume_ratio": number(item.get("lb")), "speed_pct": number(item.get("zs")),
        })
    elif pool == "sub_new":
        row.update({
            "limit_price": _price(item.get("ztp")), "opened_days": int(number(item.get("ods")) or 0) or None,
            "open_board_date": _yyyymmdd(item.get("od")), "ipo_date": _yyyymmdd(item.get("ipod")),
            "is_new_high": bool(number(item.get("nh"))),
        })
    elif pool == "broken":
        row.update({
            "limit_price": _price(item.get("ztp")), "first_seal_time": _clock(item.get("fbt")),
            "open_times": int(number(item.get("zbc")) or 0), "amplitude": number(item.get("zf")),
            "speed_pct": number(item.get("zs")),
        })
    elif pool == "limit_down":
        row.update({
            "pe": number(item.get("pe")), "seal_fund": number(item.get("fund")),
            "last_seal_time": _clock(item.get("lbt")), "board_amount": number(item.get("fba")),
            "limit_down_days": int(number(item.get("days")) or 0) or None,
            "open_times": int(number(item.get("oc")) or 0),
        })
    return row


def _yyyymmdd(value: Any) -> str | None:
    parsed = number(value)
    if parsed is None or parsed < 19900101:
        return None
    text = str(int(parsed))
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def normalize_pool(pool: str, payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    data = (payload or {}).get("data")
    items = data.get("pool") if isinstance(data, Mapping) else None
    rows = [normalize_pool_item(pool, item) for item in items or [] if isinstance(item, Mapping)]
    return [row for row in rows if row is not None]


async def fetch_pool(pool: str, trade_date: date) -> list[dict[str, Any]]:
    """Fetch one pool for ``trade_date`` (Eastmoney keeps only a recent window)."""
    spec = POOLS[pool]
    payload = await request_json("GET", f"{_BASE}/{spec.path}", params={
        "ut": _UT, "dpt": "wz.ztzt", "Pageindex": "0", "pagesize": "10000",
        "sort": spec.sort, "date": trade_date.strftime("%Y%m%d"),
    })
    if not isinstance(payload, Mapping):
        raise ValueError("Eastmoney pool response is not an object")
    return normalize_pool(pool, payload)


def pool_events(rows: list[dict[str, Any]], observed_at: datetime) -> list[dict[str, Any]]:
    """Market-event rows for the pools in :data:`MARKET_EVENT_POOLS`.

    The identity is left to the repository's per-day pool identity, so a
    re-run on the same day updates rather than duplicates.
    """
    events = []
    for row in rows:
        if row.get("pool") not in MARKET_EVENT_POOLS:
            continue
        label = POOLS[row["pool"]].label
        events.append({
            "ts_code": row["symbol"], "event_type": row["event_type"],
            "published_at": observed_at.isoformat(),
            "title": f"{label}：{row.get('name') or row['symbol']}", "url": None,
            "raw": {"capability": f"eastmoney_{row['pool']}_pool", "upstream_site": UPSTREAM_SITE, **row},
        })
    return events


def normalize_stock_change(item: Mapping[str, Any]) -> dict[str, Any] | None:
    symbol = _symbol(item)
    change_type = int(number(item.get("t")) or 0)
    clock = _clock(item.get("tm"))
    if symbol is None or not change_type or clock is None:
        return None
    info = str(item.get("i") or "")
    return {
        "symbol": symbol, "name": str(item.get("n") or "").strip() or None, "time": clock,
        "change_type": change_type, "change_label": STOCK_CHANGE_TYPES.get(change_type, str(change_type)),
        "direction": "down" if change_type in BEARISH_CHANGE_TYPES else "up",
        "info": info, "info_values": [number(part) for part in info.split(",")] if info else [],
    }


async def fetch_stock_changes(change_types: tuple[int, ...] = EVENT_CHANGE_TYPES, *,
                              page_size: int = 5000, max_pages: int = 10) -> list[dict[str, Any]]:
    """Today's cumulative anomaly tape for ``change_types`` (newest first).

    One request per type: the endpoint accepts a comma list but honours only
    its first type (measured 2026-09-18: "8201,8193" returned 8201 alone).
    """
    rows: list[dict[str, Any]] = []
    for change_type in change_types:
        for page in range(max_pages):
            payload = await request_json("GET", f"{_BASE}/getAllStockChanges", params={
                "type": str(change_type), "pageindex": str(page), "pagesize": str(page_size),
                "ut": _UT, "dpt": "wzchanges",
            })
            data = payload.get("data") if isinstance(payload, Mapping) else None
            items = data.get("allstock") if isinstance(data, Mapping) else None
            if not items:
                break
            rows.extend(row for row in (normalize_stock_change(item) for item in items if isinstance(item, Mapping)) if row)
            if len(items) < page_size:
                break
    return rows


def stock_change_events(rows: list[dict[str, Any]], trade_date: date) -> list[dict[str, Any]]:
    """One event per (symbol, type, second); the tape is re-read cumulatively."""
    stamp = trade_date.isoformat()
    events = []
    for row in rows:
        published = f"{stamp}T{row['time']}+08:00"
        events.append({
            "ts_code": row["symbol"], "event_type": "stock_change", "published_at": published,
            "title": f"盘口异动：{row.get('name') or row['symbol']} {row['change_label']}", "url": None,
            "event_identity_key": f"{PROVIDER_KEY}:stock_change:{row['symbol']}:{stamp}:{row['change_type']}:{row['time']}",
            "raw": {"capability": "stock_change", "upstream_site": UPSTREAM_SITE, **row},
        })
    return events


def stock_change_summary(rows: list[dict[str, Any]], trade_date: date) -> dict[str, Any]:
    """Daily per-type and per-symbol anomaly counts as one aggregate record."""
    by_type: dict[str, int] = {}
    by_symbol: dict[str, dict[str, int]] = {}
    for row in rows:
        label = row["change_label"]
        by_type[label] = by_type.get(label, 0) + 1
        counts = by_symbol.setdefault(row["symbol"], {})
        counts[label] = counts.get(label, 0) + 1
    return {"trade_date": trade_date.isoformat(), "rows": len(rows), "by_type": by_type, "by_symbol": by_symbol}


def normalize_board_change(item: Mapping[str, Any]) -> dict[str, Any] | None:
    code = str(item.get("c") or "").strip().upper()
    if not code.startswith("BK"):
        return None
    top = item.get("ms") if isinstance(item.get("ms"), Mapping) else {}
    counts = {}
    for entry in item.get("ydl") or []:
        if isinstance(entry, Mapping) and number(entry.get("t")) is not None:
            change_type = int(number(entry.get("t")) or 0)
            counts[STOCK_CHANGE_TYPES.get(change_type, str(change_type))] = int(number(entry.get("ct")) or 0)
    return {
        "board_code": code, "board_name": str(item.get("n") or "").strip() or None,
        "pct_change": number(item.get("u")), "main_net_inflow": number(item.get("zjl")),
        "change_count": int(number(item.get("ct")) or 0),
        "top_symbol": _symbol(top) if top else None,
        "top_change": STOCK_CHANGE_TYPES.get(int(number(top.get("t")) or 0)) if top else None,
        "change_counts": counts,
    }


async def fetch_board_changes(limit: int = 300) -> tuple[list[dict[str, Any]], str | None]:
    """Boards ranked by anomaly count, and the upstream ``dt`` stamp."""
    payload = await request_json("GET", f"{_BASE}/getAllBKChanges", params={
        "ut": _UT, "dpt": "wzchanges", "pageindex": "0", "pagesize": str(max(1, min(limit, 2000))),
    })
    data = payload.get("data") if isinstance(payload, Mapping) else None
    items = data.get("allbk") if isinstance(data, Mapping) else None
    rows = [row for row in (normalize_board_change(item) for item in items or [] if isinstance(item, Mapping)) if row]
    stamp = str(data.get("dt")) if isinstance(data, Mapping) and data.get("dt") else None
    return rows, stamp


__all__ = [
    "BEARISH_CHANGE_TYPES", "EVENT_CHANGE_TYPES", "MARKET_EVENT_POOLS", "POOLS", "PROVIDER_KEY",
    "STOCK_CHANGE_TYPES", "STRONG_POOL_REASONS", "UPSTREAM_SITE", "fetch_board_changes", "fetch_pool",
    "fetch_stock_changes", "normalize_board_change", "normalize_pool", "normalize_pool_item",
    "normalize_stock_change", "pool_events", "stock_change_events", "stock_change_summary",
]
