"""Drill a board's fast flow down to the members actually driving it.

A board net-inflow surge names a sector, not a stock.  This turns one into the
other using evidence already on hand: the rotation events computed from the
stored flow curve, the exact membership held for that taxonomy, and the all-A
cross-section the scan fetched anyway.  No per-symbol provider call is made
here - confirming the shortlist with large-order flow is a separate, bounded
step precisely because it costs one call per name.

A member qualifies only when its own move agrees with its board's direction.
The rank is led by relative strength - the member's move minus its board's -
which is the same divergence the leader-flow research uses: a stock merely
carried by its sector is not the one the money went into.

Research evidence, never an order.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

#: A board contributes at most this many names, so one crowded sector cannot
#: fill the whole shortlist.
MAX_PER_BOARD = 5

#: Below this the member is moving with the tape, not leading its board.
MIN_RELATIVE_STRENGTH_PCT = 0.5

#: A name with no turnover cannot be said to have received flow at all.
MIN_TURNOVER = 0.0

#: Delivery bounds, mirroring the leader-flow alert budget. The flow curve
#: produces a snapshot every minute and a single minute can yield a dozen
#: names, so without these one afternoon would be several hundred messages.
MAX_DELIVERED_PER_PASS = 3
MAX_DELIVERED_PER_SESSION = 24


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _agrees(direction: str, pct_change: float) -> bool:
    return pct_change > 0 if direction == "inflow" else pct_change < 0


def board_members(membership: Mapping[str, set[str]], sector_key: str) -> list[str]:
    """Symbols mapped to one board by the exact membership table."""
    return sorted(symbol for symbol, sectors in membership.items() if sector_key in sectors)


def drill_board_event(
    event: Mapping[str, Any],
    membership: Mapping[str, set[str]],
    quotes: Mapping[str, Mapping[str, Any]],
    *,
    max_per_board: int = MAX_PER_BOARD,
    min_relative_strength_pct: float = MIN_RELATIVE_STRENGTH_PCT,
) -> list[dict[str, Any]]:
    """Rank one board's members by how much they lead its own move."""
    direction = str(event.get("direction") or "")
    if direction not in {"inflow", "outflow"}:
        return []
    sector_key = str(event.get("sector_key") or "")
    board_pct = _number(event.get("change_pct")) or 0.0
    members = board_members(membership, sector_key)
    quoted = [
        (symbol, quotes[symbol]) for symbol in members
        if symbol in quotes and _number(quotes[symbol].get("pct_change")) is not None
    ]
    board_turnover = sum(_number(quote.get("turnover")) or 0.0 for _symbol, quote in quoted)
    candidates: list[dict[str, Any]] = []
    for symbol, quote in quoted:
        pct_change = _number(quote.get("pct_change"))
        if pct_change is None or not _agrees(direction, pct_change):
            continue
        relative_strength = pct_change - board_pct
        # Signed against the board's direction, so an outflow board's leader is
        # the name falling hardest rather than the one holding up best.
        leads = relative_strength if direction == "inflow" else -relative_strength
        if leads < min_relative_strength_pct:
            continue
        turnover = _number(quote.get("turnover")) or 0.0
        if turnover <= MIN_TURNOVER:
            continue
        candidates.append({
            "symbol": symbol,
            "name": quote.get("name"),
            "taxonomy_key": str(event.get("taxonomy_key") or ""),
            "sector_key": sector_key,
            "board_label": str(event.get("label") or sector_key),
            "direction": direction,
            "event_type": str(event.get("event_type") or ""),
            "board_delta_net_inflow": _number(event.get("delta_net_inflow")),
            "board_change_pct": board_pct,
            "pct_change": round(pct_change, 4),
            "relative_strength_pct": round(leads, 4),
            "turnover": turnover,
            "turnover_share": round(turnover / board_turnover, 6) if board_turnover > 0 else None,
            "board_members": len(members),
            "board_quoted_members": len(quoted),
            "evidence": "board_flow_delta + exact_membership + all_a_cross_section",
            "decision_eligible": False,
        })
    candidates.sort(key=lambda item: (
        -float(item["relative_strength_pct"]),
        -(item["turnover_share"] or 0.0),
        item["symbol"],
    ))
    for rank, item in enumerate(candidates[:max_per_board], start=1):
        item["board_rank"] = rank
    return candidates[:max_per_board]


def drill_board_events(
    events: Iterable[Mapping[str, Any]],
    membership: Mapping[str, set[str]],
    quotes: Mapping[str, Mapping[str, Any]],
    *,
    max_per_board: int = MAX_PER_BOARD,
    max_total: int = 20,
    min_relative_strength_pct: float = MIN_RELATIVE_STRENGTH_PCT,
) -> dict[str, Any]:
    """Drill every moving board, keeping the strongest names overall.

    Boards with no mapped members are reported rather than dropped: a board the
    membership cannot resolve is a coverage gap, and silently returning fewer
    names would present it as a quiet sector.
    """
    picked: list[dict[str, Any]] = []
    unmapped: list[str] = []
    for event in events:
        sector_key = str(event.get("sector_key") or "")
        if not board_members(membership, sector_key):
            unmapped.append(sector_key)
            continue
        picked.extend(drill_board_event(
            event, membership, quotes,
            max_per_board=max_per_board,
            min_relative_strength_pct=min_relative_strength_pct,
        ))
    picked.sort(key=lambda item: (
        -abs(item["board_delta_net_inflow"] or 0.0),
        -float(item["relative_strength_pct"]),
        item["symbol"],
    ))
    return {
        "candidates": picked[:max_total],
        "boards_drilled": len({str(item["sector_key"]) for item in picked}),
        "boards_without_membership": sorted(set(unmapped)),
        "min_relative_strength_pct": min_relative_strength_pct,
        "semantics": "board flow names a sector; relative strength names the member leading it",
        "decision_eligible": False,
    }


def delivery_key(candidate: Mapping[str, Any]) -> str:
    """One name, in one board, in one direction, is one finding."""
    return ":".join((
        str(candidate.get("symbol") or ""),
        str(candidate.get("taxonomy_key") or ""),
        str(candidate.get("sector_key") or ""),
        str(candidate.get("direction") or ""),
    ))


def attach_names(candidates: Iterable[Mapping[str, Any]],
                 names: Mapping[str, str]) -> list[dict[str, Any]]:
    """Fill in instrument names the cross-section does not carry.

    The all-A snapshot returns symbol, price, change and turnover but no name,
    so a delivered message would otherwise be a list of six-digit codes.
    """
    resolved: list[dict[str, Any]] = []
    for candidate in candidates:
        item = dict(candidate)
        symbol = str(item.get("symbol") or "")
        item["name"] = item.get("name") or names.get(symbol) or symbol
        resolved.append(item)
    return resolved


def select_for_delivery(
    candidates: Iterable[Mapping[str, Any]],
    already_delivered: Iterable[str],
    *,
    max_per_pass: int = MAX_DELIVERED_PER_PASS,
    max_per_session: int = MAX_DELIVERED_PER_SESSION,
) -> dict[str, Any]:
    """Pick what is worth sending, once per finding and within the budget.

    Repetition is the failure mode to avoid: the same leader keeps qualifying
    minute after minute while its board stays hot, and re-sending it says
    nothing new. The remaining budget is derived from what the session already
    delivered, so a restart cannot hand out a fresh allowance.
    """
    seen = set(already_delivered)
    remaining = max(0, min(max_per_pass, max_per_session - len(seen)))
    selected: list[dict[str, Any]] = []
    suppressed_repeat = 0
    for candidate in candidates:
        key = delivery_key(candidate)
        if key in seen:
            suppressed_repeat += 1
            continue
        if len(selected) >= remaining:
            break
        seen.add(key)
        selected.append({**dict(candidate), "delivery_key": key})
    return {
        "selected": selected,
        "suppressed_repeat": suppressed_repeat,
        "session_delivered": len(set(already_delivered)),
        "session_budget": max_per_session,
        "remaining_after": max(0, max_per_session - len(seen)),
    }


__all__ = [
    "MAX_DELIVERED_PER_PASS", "MAX_DELIVERED_PER_SESSION",
    "MAX_PER_BOARD", "MIN_RELATIVE_STRENGTH_PCT",
    "attach_names", "board_members", "delivery_key",
    "drill_board_event", "drill_board_events", "select_for_delivery",
]
