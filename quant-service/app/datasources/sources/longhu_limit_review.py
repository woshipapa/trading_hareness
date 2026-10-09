"""开盘啦涨停复盘 (DailyLimitResumption.GetPlateInfo_w38), decoded.

The review groups the session's limit-up stocks by theme plate.  Each stock is
a positional array the vendor never documents, so the positions here were
decoded on 2026-10-09 from the 2026-10-08 review and checked against an
independent source, xuangubao's public limit-up pool for the same session:
the first-seal time matched to the second for 8 of 10 stocks (1 s and 51 s
for the other two, both at the opening auction) and the consecutive-board
count (position 10) matched for all 10 (tests/fixtures/
longhu_limit_review_20261008.json, xuangubao_limit_up_20261008.json).

Two positions carry the vendor's own definitions and are named so:
``turnover_rate_vendor`` runs 1.3-2.1x xuangubao's turnover ratio, not a fixed
multiple, so it is not the total-share turnover; ``seal_amount`` is the
buy-side seal in CNY.  Positions 2, 3, 5 and 18 were constant or unexplained,
and position 7 equalled the board count in all 44 rows of that review - no
stock there had a gap, so whether it counts days (N天M板) is unproven.  They
are kept verbatim under ``undecoded`` rather than given a meaning.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..http import ashare_symbol, number

CN_TZ = ZoneInfo("Asia/Shanghai")
SOURCE = "longhuvip:DailyLimitResumption.GetPlateInfo_w38"
#: Position -> field, as decoded above.
POSITIONS = {
    0: "code", 1: "name", 4: "pct_change", 6: "first_limit_up_at", 8: "seal_amount",
    9: "board_label", 10: "board_count", 11: "concepts", 12: "main_net", 13: "turnover",
    14: "turnover_rate_vendor", 15: "float_market_value", 16: "theme", 17: "reason",
}
UNDECODED = (2, 3, 5, 7, 18)
ROW_LENGTH = 19


def _clock(value: Any, session: date) -> str | None:
    """A first-seal epoch, kept only when it falls on the review's own session."""
    seconds = number(value)
    if seconds is None or seconds < 1e9:
        return None
    moment = datetime.fromtimestamp(seconds, CN_TZ)
    return moment.isoformat() if moment.date() == session else None


def _integer(value: Any) -> int | None:
    parsed = number(value)
    return int(parsed) if parsed is not None else None


def review_session(payload: Mapping[str, Any]) -> date | None:
    try:
        return date.fromisoformat(str(payload.get("date") or "")[:10])
    except ValueError:
        return None


def decode_review(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One row per limit-up stock, with every plate the review files it under."""
    session = review_session(payload)
    if session is None:
        return []
    by_symbol: dict[str, dict[str, Any]] = {}
    for plate in payload.get("list") or []:
        if not isinstance(plate, Mapping):
            continue
        plate_ref = {"code": str(plate.get("ZSCode") or ""), "name": str(plate.get("ZSName") or "")}
        for item in plate.get("StockList") or []:
            if not isinstance(item, (list, tuple)) or len(item) < ROW_LENGTH:
                continue
            symbol = ashare_symbol(item[0])
            if symbol is None:
                continue
            existing = by_symbol.get(symbol)
            if existing is not None:
                existing["plates"].append(plate_ref)
                continue
            by_symbol[symbol] = {
                "symbol": symbol, "name": str(item[1] or ""), "trade_date": session.isoformat(),
                "pct_change": number(item[4]), "first_limit_up_at": _clock(item[6], session),
                "board_count": _integer(item[10]), "board_label": str(item[9] or ""),
                "seal_amount": number(item[8]), "main_net": number(item[12]), "turnover": number(item[13]),
                "turnover_rate_vendor": number(item[14]), "float_market_value": number(item[15]),
                "theme": str(item[16] or ""), "concepts": [part for part in str(item[11] or "").split("、") if part],
                "reason": str(item[17] or ""), "plates": [plate_ref],
                "undecoded": {str(position): item[position] for position in UNDECODED},
                "source": SOURCE,
            }
    return list(by_symbol.values())


def market_counts(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The review's market tally; ``yestRase`` is kept under the vendor's name, its unit unproven."""
    nums = payload.get("nums") if isinstance(payload.get("nums"), Mapping) else {}
    return {
        "trade_date": (review_session(payload) or "").__str__() or None,
        "rising": _integer(nums.get("SZJS")), "falling": _integer(nums.get("XDJS")),
        "limit_up": _integer(nums.get("ZT")), "limit_down": _integer(nums.get("DT")),
        "broken_ratio_pct": number(nums.get("ZBL")), "yestRase": number(nums.get("yestRase")),
        "source": SOURCE,
    }


__all__ = ["POSITIONS", "ROW_LENGTH", "SOURCE", "UNDECODED", "decode_review", "market_counts", "review_session"]
