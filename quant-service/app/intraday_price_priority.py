"""Longhu-first price selection; source priority never bypasses freshness."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from .intraday_quote_normalization import exchange_time_status


def fresh_price_rows(
    rows: list[dict[str, Any]], *, symbols: list[str],
    merge: Callable[..., Any], freshness: Callable[..., dict[str, Any]],
    observed_at: datetime, max_age_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Filter before overlaying: an old primary must not erase a fresh fallback."""
    wanted = set(symbols)
    accepted, rejected = [], {}
    for row in rows:
        symbol = str(row.get("ts_code") or row.get("symbol") or "").upper()
        if symbol not in wanted:
            continue
        candidates: dict[str, dict[str, Any]] = {}
        merge(candidates, [row])
        quote = candidates.get(symbol)
        status = freshness(quote, observed_at, max_age_seconds).get("status") if quote else "invalid_price"
        if status == "fresh":
            accepted.append(row)
        else:
            rejected[symbol] = str(status)
    return accepted, rejected


async def primary_order_books(
    symbols: list[str], *, max_symbols: int,
    licensed: Callable[..., Awaitable[list[dict[str, Any]]]],
    fallback: Callable[..., Awaitable[list[dict[str, Any]]]],
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    max_age_seconds: float = 20.0,
) -> list[dict[str, Any]]:
    """Prefer fresh Longhu depth and fill only its gaps; preserve partial wins."""
    selected = list(dict.fromkeys(s.upper() for s in symbols))[:max_symbols]
    if not selected:
        return []

    def eligible(rows: list[dict[str, Any]], wanted: list[str]) -> list[dict[str, Any]]:
        result = {}
        observed_at = now()
        for row in rows:
            symbol = str(row.get("ts_code") or "").upper()
            try:
                valid_price = float(row.get("price") or 0) > 0
            except (ValueError, TypeError):
                valid_price = False
            # A sealed limit book may legitimately have only one side.
            if symbol not in wanted or not valid_price or not (row.get("bids") or row.get("asks")):
                continue
            state = exchange_time_status(
                {"price_trade_time": row.get("trade_time"), "price_trade_date": row.get("trade_date")},
                observed_at, max_age_seconds,
            )
            if state["status"] == "fresh":
                result[symbol] = row
        return list(result.values())

    try:
        primary = eligible(await licensed(selected, max_symbols=max_symbols), selected)
    except Exception:  # noqa: BLE001 - a licensed outage must permit independent fallback
        primary = []
    present = {row["ts_code"] for row in primary}
    missing = [s for s in selected if s not in present]
    if not missing:
        return primary
    try:
        public = eligible(await fallback(missing, max_symbols=len(missing)), missing)
    except Exception:
        if primary:
            return primary
        raise
    return [*primary, *public]
