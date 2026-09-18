"""Eastmoney Guba popularity (人气榜) and surge (飙升榜) rankings.

Unlike the THS hot list, Eastmoney serves a per-stock *history* of the daily
popularity rank (about a year), so a rank series can be backfilled instead of
only snapshotted forward.  Rankings are attention evidence, never a signal.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from ..http import ashare_symbol, number, request_json


PROVIDER_KEY = "eastmoney_hot_rank"
UPSTREAM_SITE = "emappdata.eastmoney.com"
CN_TZ = ZoneInfo("Asia/Shanghai")
_BASE = "https://emappdata.eastmoney.com/stockrank"
# The public web client's fixed identifiers; they are not credentials.
_CLIENT = {"appId": "appId01", "globalId": "786e4c21-70dc-435a-93bb-38", "marketType": ""}

RANK_LISTS: dict[str, tuple[str, str]] = {
    "popularity": ("getAllCurrentList", "东财人气榜"),
    "surge": ("getAllHisRcList", "东财飙升榜"),
}


def _prefixed(symbol: str) -> str:
    code, exchange = symbol.split(".", 1)
    return f"{exchange}{code}"


def normalize_rank_item(kind: str, item: Mapping[str, Any]) -> dict[str, Any] | None:
    symbol = ashare_symbol(item.get("sc"))
    rank = number(item.get("rk"))
    if symbol is None or rank is None:
        return None
    row: dict[str, Any] = {"symbol": symbol, "list": kind, "rank": int(rank)}
    if kind == "popularity":
        row["rank_change"] = int(number(item.get("rc")) or 0)
        row["history_rank_change"] = int(number(item.get("hisRc")) or 0)
    else:
        # The surge list is ordered by ``hrcrk``; ``rk`` is still the stock's
        # popularity rank and ``hrc`` how many places it climbed since yesterday.
        row["rank_change"] = int(number(item.get("hrc")) or 0)
        row["surge_rank"] = int(number(item.get("hrcrk")) or 0) or None
    return row


def normalize_rank_list(kind: str, payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    items = (payload or {}).get("data")
    rows = [normalize_rank_item(kind, item) for item in items or [] if isinstance(item, Mapping)]
    return [row for row in rows if row is not None]


async def fetch_rank_list(kind: str = "popularity", *, page_size: int = 100) -> list[dict[str, Any]]:
    path, _label = RANK_LISTS[kind]
    payload = await request_json("POST", f"{_BASE}/{path}", json_body={
        **_CLIENT, "pageNo": 1, "pageSize": max(1, min(page_size, 100)),
    })
    if not isinstance(payload, Mapping):
        raise ValueError("Eastmoney rank response is not an object")
    return normalize_rank_list(kind, payload)


def normalize_rank_history(symbol: str, payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Daily popularity ranks.  ``effective_at`` is that session's close."""
    rows = []
    for item in (payload or {}).get("data") or []:
        if not isinstance(item, Mapping):
            continue
        rank = number(item.get("rank"))
        try:
            day = date.fromisoformat(str(item.get("calcTime") or "")[:10])
        except ValueError:
            continue
        if rank is None:
            continue
        rows.append({
            "symbol": symbol, "list": "popularity_history", "trade_date": day.isoformat(), "rank": int(rank),
            "effective_at": datetime.combine(day, time(15, 0), CN_TZ).isoformat(),
        })
    return rows


async def fetch_rank_history(symbol: str) -> list[dict[str, Any]]:
    """About one year of daily popularity ranks for one A-share."""
    payload = await request_json("POST", f"{_BASE}/getHisList", json_body={
        **_CLIENT, "srcSecurityCode": _prefixed(symbol), "yearType": "5",
    })
    if not isinstance(payload, Mapping):
        raise ValueError("Eastmoney rank history response is not an object")
    return normalize_rank_history(symbol, payload)


__all__ = [
    "PROVIDER_KEY", "RANK_LISTS", "UPSTREAM_SITE", "fetch_rank_history", "fetch_rank_list",
    "normalize_rank_history", "normalize_rank_item", "normalize_rank_list",
]
