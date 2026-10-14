"""Deterministic settled days for the tactic-trial tests: no randomness, so two versions of the code see the same input."""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta

from app.tactic_conditions import CONDITIONS
from app.tactic_exits import EXITS

STOCK = {slot: [condition.key for condition in CONDITIONS if condition.level == "stock" and condition.slot == slot]
         for slot in ("close", "auction")}
DAY = [condition.key for condition in CONDITIONS if condition.level == "day" and condition.slot == "close"]


def _mix(left: int, right: int) -> int:
    """A fixed scramble of two integers, so that flags of different conditions do not move together."""
    return (((left * 2654435761) ^ (right * 2246822519)) & 0xFFFFFFFF) >> 9


def _value(seed: int, scale: float) -> float:
    return round((_mix(seed, 97) % 2001 - 1000) / 1000 * scale, 6)


def record_outcomes(tactic) -> tuple[str, ...] | None:
    """The outcomes kept for a single-condition tactic's records (a combination keeps every outcome: None)."""
    if not tactic.key.startswith("only."):
        return None
    return tuple(dict.fromkeys((tactic.primary, f"{tactic.entry}:{tactic.exit}", f"{tactic.entry}:close")))


def rounded(value, digits: int = 9):
    """The value with every float rounded: two computations that agree to rounding give the same text."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, dict):
        return {str(key): rounded(item, digits) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [rounded(item, digits) for item in value]
    return round(float(value), digits) + 0.0


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(rounded(value), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def settled_day(index: int, slot: str, first: date = date(2026, 8, 20), rows: int = 12) -> dict:
    """One settled day: ``rows`` stocks with flags, cell fields and every outcome, all functions of ``index``."""
    items, outcomes = [], {}
    for position in range(rows):
        seed = index * 131 + position * 17 + (1 if slot == "auction" else 0)
        flags = [key for offset, key in enumerate(STOCK[slot]) if _mix(seed, offset) % 3 == 0]
        unknown = [key for offset, key in enumerate(STOCK[slot]) if key not in flags and _mix(seed, offset + 500) % 23 == 0]
        row = {"symbol": f"{position:06d}.SZ", "name": f"S{position}", "boards": 1 + position % 4,
               "board_bucket": ("1", "2", "3+")[min(2, position % 4)], "gap_bucket": ("<0", "0-5", "5+")[seed % 3],
               "flags": flags, "unknown": unknown}
        if slot == "auction":
            row["prev"] = {"flags": [key for offset, key in enumerate(STOCK["close"]) if _mix(seed, offset + 900) % 3 == 0], "unknown": []}
            row["flags"] = flags + [f"prev.{key}" for key in row["prev"]["flags"]]
        items.append(row)
        entries = {entry: {exit_.key: (
            {"status": "no_trade", "raw_return": None, "net_return": None} if _mix(seed, number + 300) % 19 == 0 else
            {"status": "settled", "raw_return": _value(seed + number, 0.06), "net_return": _value(seed + number, 0.06) - 0.001})
            for number, exit_ in enumerate(EXITS, start=3 if entry == "held" else 11)} for entry in ("held", "buy_open")}
        outcomes[row["symbol"]] = {
            "entries": entries, "seal": _mix(seed, 41) % 3 == 0, "big_loss": _mix(seed, 43) % 11 == 0, "open_to_close": _value(seed + 1, 0.03),
            "s1_high": abs(_value(seed + 2, 0.08)), "s3_high": abs(_value(seed + 4, 0.15)), "s1_low": -abs(_value(seed + 5, 0.06))}
    return {"trade_date": (first + timedelta(days=index)).isoformat(), "slot": slot,
            "day": {"flags": [key for offset, key in enumerate(DAY) if _mix(index, offset + 700) % 2 == 0],
                    "unknown": [key for offset, key in enumerate(DAY) if _mix(index, offset + 700) % 2 and _mix(index, offset + 800) % 7 == 0]},
            "rows": items, "outcomes": outcomes}


def settled_days(count: int = 130) -> list[dict]:
    return [settled_day(index, slot) for index in range(count) for slot in ("close", "auction")]
