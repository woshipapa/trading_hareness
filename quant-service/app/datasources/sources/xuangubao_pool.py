"""选股宝 public limit pools (flash-api.xuangubao.com.cn /api/pool/detail).

The pool fills what the Longhu review and the Fuyao pool do not carry: the
last seal time, how many times the seal broke, the N天M板 count and whether
the stock is new.  It is public and unauthenticated, so it is a second
source beside the licensed one, and its turnover ratio is its own definition
(see longhu_limit_review for how the two differ).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..http import ashare_symbol, number, request_json

CN_TZ = ZoneInfo("Asia/Shanghai")
URL = "https://flash-api.xuangubao.com.cn/api/pool/detail"
SOURCE = "xuangubao:pool"
POOLS = ("limit_up", "limit_up_broken", "limit_down", "yesterday_limit_up")


def _clock(value: Any, session: date) -> str | None:
    seconds = number(value)
    if seconds is None or seconds < 1e9:
        return None
    moment = datetime.fromtimestamp(seconds, CN_TZ)
    return moment.isoformat() if moment.date() == session else None


def _integer(value: Any) -> int | None:
    parsed = number(value)
    return int(parsed) if parsed is not None else None


def pool_symbol(value: Any) -> str | None:
    """选股宝 writes Shanghai as ``.SS``; read as-is it would drop every Shanghai stock."""
    text = str(value or "").strip().upper()
    return ashare_symbol(text[:-3] + ".SH" if text.endswith(".SS") else text)


def decode_pool(rows: list[Mapping[str, Any]], pool_name: str, session: date) -> list[dict[str, Any]]:
    decoded = []
    for row in rows:
        symbol = pool_symbol(row.get("symbol"))
        if symbol is None:
            continue
        reason = row.get("surge_reason") if isinstance(row.get("surge_reason"), Mapping) else {}
        decoded.append({
            "symbol": symbol, "name": row.get("stock_chi_name"), "trade_date": session.isoformat(), "pool": pool_name,
            "first_limit_up_at": _clock(row.get("first_limit_up"), session),
            "last_limit_up_at": _clock(row.get("last_limit_up"), session),
            "break_times": _integer(row.get("break_limit_up_times")),
            "limit_up_days": _integer(row.get("limit_up_days")),
            "m_days": _integer(row.get("m_days_n_boards_days")), "n_boards": _integer(row.get("m_days_n_boards_boards")),
            "is_new_stock": bool(row.get("is_new_stock")), "change_percent": number(row.get("change_percent")),
            "turnover_ratio_vendor": number(row.get("turnover_ratio")),
            "buy_lock_volume_ratio": number(row.get("buy_lock_volume_ratio")),
            "reason": reason.get("stock_reason"),
            "related_plates": [plate.get("plate_name") for plate in reason.get("related_plates") or []
                               if isinstance(plate, Mapping) and plate.get("plate_name")],
            "source": SOURCE,
        })
    return decoded


async def fetch_pool(pool_name: str, session: date) -> list[dict[str, Any]]:
    """One session's pool, decoded; an unknown pool name is refused before any request."""
    if pool_name not in POOLS:
        raise ValueError(f"unknown xuangubao pool: {pool_name}")
    payload = await request_json("GET", URL, params={"pool_name": pool_name, "date": session.isoformat()},
                                 timeout_seconds=20.0)
    rows = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        raise ValueError("xuangubao pool response has no data list")
    return decode_pool(rows, pool_name, session)


__all__ = ["POOLS", "SOURCE", "URL", "decode_pool", "fetch_pool", "pool_symbol"]
