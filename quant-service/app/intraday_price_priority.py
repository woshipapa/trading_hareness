"""Longhu-first price selection; source priority never bypasses freshness."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping

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


def order_book_from_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Read the ten-level book from either shape the licensed source returns.

    The single-symbol quote nests it under ``order_book``; the batched watch
    call puts ``bids``/``asks`` at the row's top level and has no such key.
    Reading only the nested shape silently discarded every batched row, so the
    order-book loop found nothing licensed and ran the whole session on the
    Tencent fallback while the licensed levels were sitting in the response.
    """
    nested = row.get("order_book")
    if isinstance(nested, Mapping) and (nested.get("bids") or nested.get("asks")):
        return dict(nested)
    bids = [dict(level) for level in (row.get("bids") or []) if isinstance(level, Mapping)]
    asks = [dict(level) for level in (row.get("asks") or []) if isinstance(level, Mapping)]
    if not bids and not asks:
        return None
    # Derived exactly as the nested shape derives them, so a consumer cannot
    # tell which call produced the book.
    side = "bid_only" if bids and not asks else "ask_only" if asks and not bids else "two_sided"
    return {
        "bids": bids, "asks": asks, "book_side": side,
        "one_sided_book": side != "two_sided",
        "seal_volume_lot": (bids[0].get("size") if side == "bid_only"
                            else asks[0].get("size") if side == "ask_only" else None),
        "total_bid_lot": row.get("total_bid_lot"),
        "total_ask_lot": row.get("total_ask_lot"),
        "source": "longhuvip:GetStockPanKou",
    }
