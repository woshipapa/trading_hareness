"""Pure post-close board and LHB evidence aggregation."""

from __future__ import annotations

from typing import Any, Callable


def exact_board_context(rows: list[dict[str, Any]], *, json_safe: Callable[[Any], Any]) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = json_safe(dict(row))
        symbol = str(item["symbol"])
        current = contexts.get(symbol)
        if current is None or float(item.get("net_amount") or 0) > float(current.get("net_amount") or 0):
            contexts[symbol] = {**item, "exact_member_mapping": True}
    positive_flows = sorted({float(item.get("net_amount") or 0) for item in contexts.values()
                             if float(item.get("net_amount") or 0) > 0})
    denominator = max(1, len(positive_flows) - 1)
    for item in contexts.values():
        flow = float(item.get("net_amount") or 0)
        item["flow_percentile"] = round(positive_flows.index(flow) / denominator, 4) if flow > 0 else 0.0
    return contexts


#: Detail the Tushare-era context filled from ``top_inst`` (every seat on the
#: list with its own buy/sell/net) and ``top_list`` (the exchange's listing
#: reason).  The Fuyao list has one row per stock -- the list's buy/sell/net
#: and the hot-money seats' combined net -- with no seat names, no
#: institution split and no listing reason, so these are reported as
#: unavailable (``None``), never as an empty list or a zero.
UNAVAILABLE_DETAIL_FIELDS: tuple[str, ...] = (
    "reasons", "institutions", "institution_count", "institution_records",
    "institution_buy", "institution_sell", "institution_net_buy",
)


def lhb_context(rows: list[dict[str, Any]], *, number: Callable[[Any], float | None]) -> dict[str, dict[str, Any]]:
    """Project one session's stored dragon-tiger list by symbol.

    ``rows`` are ``lhb_ths`` market events of a single trade date, newest
    capture first, each with its parsed ``payload``; a row whose body was not
    JSON is skipped.  Amounts are CNY totals over the seats on the list, not
    an institution-only figure.  ``hot_money_net_buy`` rests on the
    provider's own hot-money seat classification, which has no official
    source.  ``limit_reason`` is the provider's limit-up theme, not the
    exchange's listing reason.
    """
    contexts: dict[str, dict[str, Any]] = {}
    for stored in rows:
        payload = stored.get("payload")
        symbol = str(stored.get("symbol") or "").upper()
        if not isinstance(payload, dict) or not symbol or symbol in contexts:
            continue
        contexts[symbol] = {
            "trade_date": payload.get("trade_date"), "name": payload.get("name"),
            "net_buy": number(payload.get("net_value")), "buy": number(payload.get("buy_value")),
            "sell": number(payload.get("sell_value")), "hot_money_net_buy": number(payload.get("hot_money_net_value")),
            "range_days": payload.get("range_days"), "limit_reason": payload.get("limit_reason") or None,
            "concepts": [str(name) for name in payload.get("concepts") or [] if name],
            "providers": [str(stored.get("source") or "")], "available_at": stored.get("available_at"),
            "seat_detail": "unavailable", **dict.fromkeys(UNAVAILABLE_DETAIL_FIELDS),
        }
    return contexts


__all__ = ["UNAVAILABLE_DETAIL_FIELDS", "exact_board_context", "lhb_context"]
