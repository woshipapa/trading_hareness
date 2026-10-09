"""Concept limit-up strength from the session's Fuyao limit-up pool.

Tushare's ``limit_cpt_list`` was THS's own "strongest limit-up concepts" list
and ``limit_list_ths`` its limit-up pool.  Both are gone (decision 0005); what
remains is the pool Fuyao serves during the session, captured every minute
into ``quant.market_events`` (``limit_up_pool``), and the THS concept
membership Fuyao serves (``fuyao_ths_concept``).  A concept's strength is
counted here from those two, by exact code only:

* ``limit_up_count`` - point-in-time members in the session's last captured
  pool, i.e. still sealed at that minute (a name that sealed and broke is in
  the break pool, not here);
* leaders - those members ranked by consecutive boards, then the pool's
  largest seal amount, then code.

It is not THS's ranking: ``limit_cpt_list`` also weighed its own days-on-list
and consecutive-board statistics, which no available source reproduces.
Pure; the caller reads the pool and the membership.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from .datasources.http import number


def event_body(row: Mapping[str, Any]) -> dict[str, Any]:
    """The stored pool item (``market_events.body`` is JSON text)."""
    body = row.get("body")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            body = None
    return dict(body) if isinstance(body, Mapping) else {}


def board_count(item: Mapping[str, Any]) -> int:
    """Consecutive limit-up sessions; Fuyao's pool field is ``continue_day_cnt``."""
    value = number(item.get("continue_day_cnt"))
    return max(1, int(value)) if value is not None else 1


def limit_tag(item: Mapping[str, Any]) -> str:
    count = board_count(item)
    return "首板" if count <= 1 else f"{count}天{count}板"


def leader_key(symbol: str, item: Mapping[str, Any]) -> tuple[int, float, str]:
    return -board_count(item), -(number(item.get("max_seal_money")) or 0.0), symbol


def concept_strength(
    pool: Mapping[str, Mapping[str, Any]],
    memberships: Iterable[Mapping[str, Any]],
    member_counts: Mapping[str, int],
    labels: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Every concept with at least one sealed member, strongest first.

    ``memberships`` are ``(sector_key, symbol)`` rows already restricted to
    point-in-time members that are sectors; ``member_counts`` is each
    concept's point-in-time size, the denominator of ``limit_up_ratio``.
    """
    sealed: dict[str, set[str]] = {}
    for row in memberships:
        symbol, sector_key = str(row.get("symbol") or "").upper(), str(row.get("sector_key") or "")
        if sector_key and symbol in pool:
            sealed.setdefault(sector_key, set()).add(symbol)
    concepts: list[dict[str, Any]] = []
    for sector_key, symbols in sealed.items():
        ranked = sorted(symbols, key=lambda symbol: leader_key(symbol, pool[symbol]))
        members = max(int(member_counts.get(sector_key) or 0), len(symbols))
        concepts.append({
            "sector_key": sector_key, "label": labels.get(sector_key) or sector_key,
            "limit_up_count": len(symbols), "member_count": members,
            "limit_up_ratio": round(len(symbols) / members, 6),
            "max_board_count": max(board_count(pool[symbol]) for symbol in symbols),
            "limit_up_symbols": ranked,
        })
    concepts.sort(key=lambda item: (-item["limit_up_count"], -item["limit_up_ratio"], item["sector_key"]))
    for rank, item in enumerate(concepts, start=1):
        item["rank"] = rank
    return concepts


__all__ = ["board_count", "concept_strength", "event_body", "leader_key", "limit_tag"]
