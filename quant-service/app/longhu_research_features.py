"""Conservative normalisation for Longhu supplemental research responses.

The vendor uses several incompatible payload shapes.  This module keeps the
provider payload lossless and only derives a symbol when a strict six-digit
exchange code is present.  It intentionally does not guess field meanings.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Mapping


SYMBOL_RE = re.compile(r"(?:SZ|SH|BJ)?\d{6}|\d{6}\.(?:SZ|SH|BJ)", re.I)


def payload_rows(payload: Mapping[str, Any]) -> list[Any]:
    """Return the first recognised vendor collection, preserving row shape."""
    for key in ("list", "data", "items", "trend", "rows", "bid", "info"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def strict_symbol(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    match = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", text)
    if match:
        return text
    match = re.fullmatch(r"(?:SH|SZ|BJ)?(\d{6})", text)
    if not match:
        return None
    code = match.group(1)
    exchange = "SH" if code.startswith("6") else "SZ" if code.startswith(("0", "3")) else "BJ" if code.startswith(("4", "8", "9")) else None
    return f"{code}.{exchange}" if exchange else None


def _row_symbol(row: Any) -> str | None:
    if isinstance(row, Mapping):
        for key in ("ts_code", "symbol", "code", "StockID", "stock_id", "thscode"):
            symbol = strict_symbol(row.get(key))
            if symbol:
                return symbol
    # Several documented Longhu list endpoints use positional rows.  A strict
    # symbol parser over the first fields is less speculative than assigning
    # numeric columns a meaning, while still preserving symbol attribution.
    if isinstance(row, (list, tuple)):
        for value in row[:4]:
            symbol = strict_symbol(value)
            if symbol:
                return symbol
    return None


def normalize_payload(
    *, target: str, action: str, controller: str | None, payload: Mapping[str, Any],
    trade_date: date, observed_at: datetime, availability_basis: str = "owner_gateway_receipt_post_close",
    next_session_only: bool = False,
) -> list[dict[str, Any]]:
    """Create raw evidence rows with explicit clocks and semantic boundaries."""
    rows = payload_rows(payload)
    source_rows = rows if rows else [payload]
    result: list[dict[str, Any]] = []
    for index, item in enumerate(source_rows):
        symbol = _row_symbol(item)
        result.append({
            "ts_code": symbol,
            "target": target,
            "action": action,
            "controller": controller,
            "exchange_date": trade_date.isoformat(),
            "observed_at": observed_at.isoformat(),
            "available_at": observed_at.isoformat(),
            "availability_basis": availability_basis,
            "next_session_only": next_session_only,
            "research_only": True,
            "replay_only": True,
            "live_effect": "none",
            "record_index": index,
            "payload": item,
        })
    return result


def next_session_context(rows: list[Mapping[str, Any]], current_date: date) -> dict[str, Any]:
    """Summarize only supplemental evidence from earlier exchange dates."""
    grouped: dict[str, dict[str, Any]] = {}
    rejected_same_day = 0
    for row in rows:
        payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else row
        exchange_date = str(payload.get("exchange_date") or "")
        try:
            source_date = date.fromisoformat(exchange_date)
        except ValueError:
            continue
        if source_date >= current_date:
            rejected_same_day += 1
            continue
        capability = str(row.get("capability") or payload.get("capability") or "longhu:unknown")
        item = grouped.setdefault(capability, {"rows": 0, "symbols": [], "latest_available_at": None})
        item["rows"] += 1
        symbol = strict_symbol(row.get("symbol") or payload.get("ts_code"))
        if symbol and len(item["symbols"]) < 50 and symbol not in item["symbols"]:
            item["symbols"].append(symbol)
        available_at = row.get("available_at")
        if available_at is not None:
            item["latest_available_at"] = str(available_at)
    return {
        "status": "available" if grouped else "empty",
        "next_session_only": True,
        "capabilities": grouped,
        "rejected_same_day_rows": rejected_same_day,
    }


__all__ = ["next_session_context", "normalize_payload", "payload_rows", "strict_symbol"]
