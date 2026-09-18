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


__all__ = [
    "MAX_PER_BOARD", "MIN_RELATIVE_STRENGTH_PCT",
    "board_members", "drill_board_event", "drill_board_events",
]
