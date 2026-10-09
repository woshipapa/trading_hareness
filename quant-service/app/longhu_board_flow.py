"""Board flow from the licensed Longhu ranking, shaped like the public feed.

The intraday board-flow curve is the only hot-path source with no licensed
option wired in: it reads Eastmoney through akshare, which was returning
"public HTTP GET request failed after bounded retry" during the 2026-09-17
session while the licensed gateway answered every call.

Longhu's ranking endpoint carries the same quantities for all 104 industry
boards, so this normalizes them into the shape the curve already stores rather
than introducing a second one.  Board identity stays Longhu's own
(``longhu_ths_industry``): the two vendors do not agree on what a board is, and
silently mixing their keys into one series would make a rotation look like it
crossed boards when it only crossed providers.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

TAXONOMY_KEY = "longhu_ths_industry"

#: Column positions in the vendor's ranking row, which is a bare list.
_SECTOR_KEY, _LABEL, _STRENGTH, _CHANGE_PCT = 0, 1, 2, 3
_AMOUNT, _NET_INFLOW, _VOLUME_RATIO = 5, 6, 9


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def board_flow_items(rows: Iterable[Any]) -> list[dict[str, Any]]:
    """Normalize ranking rows into the stored board-flow curve shape.

    A row without a net inflow is dropped rather than stored as zero: absent
    flow and balanced flow are different observations, and the curve's rotation
    detection reads the sign.
    """
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) <= _NET_INFLOW:
            continue
        sector_key = str(row[_SECTOR_KEY] or "").strip()
        label = str(row[_LABEL] or "").strip() or sector_key
        net_inflow = _number(row[_NET_INFLOW])
        if not sector_key or sector_key in seen or net_inflow is None:
            continue
        seen.add(sector_key)
        items.append({
            "taxonomy_key": TAXONOMY_KEY,
            "sector_key": sector_key,
            "label": label,
            "net_inflow": round(net_inflow, 6), "unit": "cny",
            "change_pct": _number(row[_CHANGE_PCT]),
            "strength": _number(row[_STRENGTH]),
            "amount": _number(row[_AMOUNT]),
            "volume_ratio": _number(row[_VOLUME_RATIO]) if len(row) > _VOLUME_RATIO else None,
        })
    items.sort(key=lambda item: (-float(item["net_inflow"]), str(item["sector_key"])))
    return items


def gateway_rows(payload: Mapping[str, Any]) -> list[Any]:
    """Collect list rows from the gateway's page envelope.

    The licensed gateway nests each documented call's body under
    ``pages[].payload``; reading ``list`` from the response root returns
    nothing at all and looks exactly like an empty vendor.
    """
    rows: list[Any] = []
    for page in payload.get("pages") or []:
        body = (page or {}).get("payload") or {}
        if isinstance(body, Mapping):
            rows.extend(body.get("list") or [])
    return rows


__all__ = ["TAXONOMY_KEY", "board_flow_items", "gateway_rows"]
