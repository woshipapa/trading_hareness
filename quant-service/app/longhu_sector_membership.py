"""Full-market sector membership from the licensed Longhu gateway.

Leader-flow cannot name a candidate without knowing its sector: every pool
member is rejected as ``sector_core_unconfirmed`` while
``quant.sector_membership_history`` is empty, which is the state the peer was
in for the whole of 2026-09-17 and the morning of 2026-09-18.

The concept-flow taxonomy is filled one board at a time through a rate-limited
Tushare route and takes hours.  Longhu already serves the same thing as a
licensed product: 104 industry boards, each returning its complete member list
in one gateway call.

Membership is read for a *completed* session.  The live parameter form the
vendor exposes for this call is rejected outright (``errcode 1020``), and that
is no loss: a board's constituents do not change during the session, so the
prior close is both the available answer and the point-in-time honest one.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable, Iterable, Mapping

#: Longhu's own industry taxonomy, kept distinct from the Tushare-sourced
#: ``ths_concept_flow`` and ``ths_industry`` so a reader always knows which
#: vendor's definition of a sector it is holding.
TAXONOMY_KEY = "longhu_ths_industry"
PROVIDER_KEY = "longhuvip"

#: The ranking endpoint ignores a larger page size and returns eight boards per
#: call regardless, so the page cursor advances by what actually arrived.
CATALOG_PAGE_SIZE = 100
CATALOG_MAX_PAGES = 30
MEMBER_PAGE_SIZE = 300


def gateway_rows(payload: Mapping[str, Any]) -> list[Any]:
    """Collect list rows from a gateway response's page envelope.

    The licensed gateway returns each documented call's body under
    ``pages[].payload`` rather than at the top level; reading ``list`` directly
    from the response silently yields nothing.
    """
    rows: list[Any] = []
    for page in payload.get("pages") or []:
        body = (page or {}).get("payload") or {}
        if isinstance(body, Mapping):
            rows.extend(body.get("list") or [])
    return rows


def gateway_count(payload: Mapping[str, Any]) -> int | None:
    for page in payload.get("pages") or []:
        body = (page or {}).get("payload") or {}
        if isinstance(body, Mapping) and body.get("Count") is not None:
            try:
                return int(body["Count"])
            except (TypeError, ValueError):
                return None
    return None


def catalog_request(offset: int) -> dict[str, Any]:
    return {
        "target": "longhu_market", "path": "/w1/api/index.php",
        "params": {"a": "RealRankingInfo", "c": "ZhiShuRanking", "Order": 1,
                   "st": CATALOG_PAGE_SIZE, "apiv": "w26", "Type": 1,
                   "Index": offset, "ZSType": 4},
    }


def member_request(plate_id: str, trading_date: date, offset: int = 0) -> dict[str, Any]:
    return {
        "target": "longhu_market_wide", "path": "/w1/api/index.php",
        "params": {"a": "ZhiShuStockList_W8", "c": "ZhiShuRanking", "Order": 1,
                   "st": MEMBER_PAGE_SIZE, "old": 1, "IsZZ": 0, "Index": offset,
                   "REnd": 1500, "apiv": "w41", "Type": 6, "IsKZZType": 0,
                   "PlateID": str(plate_id), "TSZB_Type": 0, "filterType": 0,
                   "Date": trading_date.isoformat()},
    }


def parse_catalog(rows: Iterable[Any]) -> list[dict[str, str]]:
    """Board id and label from the ranking rows, ignoring malformed entries."""
    boards: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, list) or not row:
            continue
        sector_key = str(row[0] or "").strip()
        if not sector_key or sector_key in seen:
            continue
        seen.add(sector_key)
        boards.append({"sector_key": sector_key,
                       "label": str(row[1] if len(row) > 1 else sector_key)})
    return boards


def fetch_catalog(raw_call: Callable[[Mapping[str, Any]], Mapping[str, Any]]) -> list[dict[str, str]]:
    """Every industry board, paging until the vendor's own Count is reached."""
    boards: list[dict[str, str]] = []
    seen: set[str] = set()
    expected: int | None = None
    offset = 0
    for _ in range(CATALOG_MAX_PAGES):
        payload = raw_call(catalog_request(offset))
        rows = gateway_rows(payload)
        expected = expected if expected is not None else gateway_count(payload)
        if not rows:
            break
        page = [board for board in parse_catalog(rows) if board["sector_key"] not in seen]
        seen.update(board["sector_key"] for board in page)
        boards.extend(page)
        # The cursor advances by what arrived, not by the size requested: this
        # endpoint returns eight rows however many are asked for.
        offset += len(rows)
        if expected is not None and len(boards) >= expected:
            break
    return boards


__all__ = [
    "CATALOG_MAX_PAGES", "CATALOG_PAGE_SIZE", "MEMBER_PAGE_SIZE", "PROVIDER_KEY", "TAXONOMY_KEY",
    "catalog_request", "fetch_catalog", "gateway_count", "gateway_rows",
    "member_request", "parse_catalog",
]
