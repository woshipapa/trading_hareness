"""One full roster and one dated price contract for owner and shared gateway."""
from datetime import date
from typing import Any, Iterable


def fetch(source: Any, trade_date: date, extra_symbols: Iterable[str] = (), *,
          quote_source: Any | None = None) -> dict[str, Any]:
    from .longhu_settled_quotes import fetch as fetch_quotes
    from .longhu_board_close import aggregate_board
    catalog = source.industry_plate_catalog()
    vendor, vendor_health = source.full_market_vendor_rows(
        trade_date, plate_ids=[r["sector_key"] for r in catalog])
    off_plate = [s for s in dict.fromkeys(extra_symbols) if s not in vendor]
    quotes, quote_health = fetch_quotes(quote_source if quote_source is not None else source,
        list(vendor) + off_plate, trade_date, workers=8)
    quote_health.update(plate_symbols=len(vendor), off_plate_requested=len(off_plate))
    members: dict[str, list[dict[str, Any]]] = {}
    for row in vendor.values():
        members.setdefault(str(row["plate_id"]), []).append(row)
    return {"trade_date": trade_date, "vendor_rows": vendor, "quote_rows": quotes,
            "board_rows": [aggregate_board(board, members.get(board["sector_key"], []), trade_date)
                           for board in catalog],
            "health": {"longhu": vendor_health, "licensed_ohlc": quote_health}}
