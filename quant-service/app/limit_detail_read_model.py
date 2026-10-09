"""One session's limit-up stocks in full detail, Longhu first, 选股宝 beside it.

Reads what the close's supplemental capture archived: the 开盘啦 limit review
(DailyLimitResumption.GetPlateInfo_w38, decoded by longhu_limit_review) and
选股宝's public limit-up pool.  Each stock carries both decodings, the merged
fields a strategy reads, which source each came from, and how far the two
sources' first-seal times are apart - so a disagreement shows instead of
being averaged away.

A review asked for "the latest" can be the previous session's when it is
captured before the vendor publishes, so only review pages whose own date is
the requested session are used.  Research evidence, never an order.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from .datasources.sources.longhu_limit_review import decode_review
from .datasources.sources.xuangubao_pool import decode_pool

CN_TZ = ZoneInfo("Asia/Shanghai")
REVIEW_CAPABILITY = "longhu:longhu_market_wide:GetPlateInfo_w38"
POOL_CAPABILITY_PREFIX = "longhu:xuangubao:pool:"
AUCTION_SEAL = time(9, 25, 59)


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        loaded = json.loads(value) if value else {}
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _stored(connection: Any, capability_pattern: str, trade_date: date) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT capability,normalized FROM quant.raw_market_observations
            WHERE provider_key='longhuvip' AND capability LIKE %s
              AND normalized->>'exchange_date'=%s
            ORDER BY available_at,capability""",
        (capability_pattern, trade_date.isoformat()),
    ).fetchall()
    return [{"capability": dict(row)["capability"], **_payload(dict(row)["normalized"])} for row in rows]


def _seconds_apart(first: str | None, second: str | None) -> float | None:
    if not first or not second:
        return None
    return abs((datetime.fromisoformat(first) - datetime.fromisoformat(second)).total_seconds())


def merge_limit_detail(longhu_rows: list[Mapping[str, Any]], pool_rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    longhu = {row["symbol"]: row for row in longhu_rows}
    pool = {row["symbol"]: row for row in pool_rows if row.get("pool") == "limit_up"}
    merged = []
    for symbol in sorted(set(longhu) | set(pool)):
        lh, xg = longhu.get(symbol), pool.get(symbol)
        first = (lh or {}).get("first_limit_up_at") or (xg or {}).get("first_limit_up_at")
        last = (xg or {}).get("last_limit_up_at")
        breaks = (xg or {}).get("break_times")
        one_word = None
        if first and last and breaks is not None:
            one_word = (breaks == 0 and datetime.fromisoformat(first).time() <= AUCTION_SEAL
                        and datetime.fromisoformat(last).time() <= AUCTION_SEAL)
        merged.append({
            "symbol": symbol, "name": (lh or xg or {}).get("name"),
            "first_limit_up_at": first, "first_limit_up_source": "longhuvip" if (lh or {}).get("first_limit_up_at") else "xuangubao" if first else None,
            "last_limit_up_at": last, "break_times": breaks, "one_word": one_word,
            "board_count": (lh or {}).get("board_count") if lh else (xg or {}).get("limit_up_days"),
            "board_label": (lh or {}).get("board_label"),
            "m_days": (xg or {}).get("m_days"), "n_boards": (xg or {}).get("n_boards"),
            "seal_amount": (lh or {}).get("seal_amount"), "main_net": (lh or {}).get("main_net"),
            "turnover": (lh or {}).get("turnover"), "turnover_rate_vendor": (lh or {}).get("turnover_rate_vendor"),
            "float_market_value": (lh or {}).get("float_market_value"),
            "theme": (lh or {}).get("theme"), "concepts": (lh or {}).get("concepts") or [],
            "plates": (lh or {}).get("plates") or [],
            "reason": (lh or {}).get("reason") or (xg or {}).get("reason"),
            "is_new_stock": (xg or {}).get("is_new_stock"),
            "agreement": {"first_seal_gap_seconds": _seconds_apart((lh or {}).get("first_limit_up_at"),
                                                                    (xg or {}).get("first_limit_up_at")),
                          "board_count_equal": (lh["board_count"] == xg["limit_up_days"]) if lh and xg else None},
            "sources": [name for name, row in (("longhuvip", lh), ("xuangubao", xg)) if row],
        })
    return merged


def _page_on_session(page: Mapping[str, Any], trade_date: date) -> bool:
    """For a page archived before the vendor's date was kept: do most first seals fall on the session?"""
    stamps = [item[6] for item in page.get("StockList") or [] if isinstance(item, (list, tuple)) and len(item) > 6]
    on_day = sum(1 for stamp in stamps if isinstance(stamp, (int, float)) and stamp > 1e9
                 and datetime.fromtimestamp(stamp, CN_TZ).date() == trade_date)
    return bool(stamps) and on_day * 2 >= len(stamps)


def limit_detail_day(connection: Any, trade_date: date) -> dict[str, Any]:
    reviews, undated = [], 0
    for row in _stored(connection, REVIEW_CAPABILITY, trade_date):
        page = row.get("payload") if isinstance(row.get("payload"), Mapping) else None
        if page is None:
            continue
        if row.get("source_date") is None:
            undated += 1
            if not _page_on_session(page, trade_date):
                continue
        elif row["source_date"] != trade_date.isoformat():
            continue
        reviews.append(row)
    longhu_rows = decode_review({"date": trade_date.isoformat(),
                                 "list": [row.get("payload") for row in reviews if isinstance(row.get("payload"), Mapping)]})
    pool_rows: list[dict[str, Any]] = []
    for row in _stored(connection, POOL_CAPABILITY_PREFIX + "%", trade_date):
        pool_name = str(row["capability"]).removeprefix(POOL_CAPABILITY_PREFIX)
        if isinstance(row.get("payload"), Mapping):
            pool_rows.extend(decode_pool([row["payload"]], pool_name, trade_date))
    stocks = merge_limit_detail(longhu_rows, pool_rows)
    return {
        "trade_date": trade_date.isoformat(), "stocks": stocks,
        "broken": [row for row in pool_rows if row["pool"] == "limit_up_broken"],
        "limit_down": [row for row in pool_rows if row["pool"] == "limit_down"],
        "coverage": {"longhu_review": len(longhu_rows), "xuangubao_limit_up": sum(row["pool"] == "limit_up" for row in pool_rows),
                     "both": sum(len(stock["sources"]) == 2 for stock in stocks),
                     "review_pages_without_own_date": undated},
        "status": "completed" if stocks else "missing",
        "research_only": True, "live_effect": "none",
    }


__all__ = ["POOL_CAPABILITY_PREFIX", "REVIEW_CAPABILITY", "limit_detail_day", "merge_limit_detail"]
