"""One minute's Eastmoney board cross-section, normalized for the board-flow curve.

Pure; moved out of main.py (docs/decisions/0008).
"""

from __future__ import annotations

from statistics import median
from typing import Any

from .numeric_utils import intraday_number


def intraday_board_flow_curve_items(kind: str, flows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize one Eastmoney board cross-section without stock-level joins.

    The public response can repeat a display board while paginating.  One
    minute stores one value per exact upstream key/label; the median makes a
    tiny between-page timing difference deterministic without treating the
    duplicate as a second board.
    """
    if kind not in {"concept", "industry"}:
        raise ValueError("kind must be concept or industry")
    grouped: dict[tuple[str, str], list[dict[str, float | None]]] = {}
    for flow in flows:
        label = str(flow.get("行业") or flow.get("板块名称") or "").strip()
        sector_key = str(flow.get("行业代码") or flow.get("板块代码") or label).strip()
        if not label or not sector_key:
            continue
        inflow, outflow = intraday_number(flow.get("流入资金")), intraday_number(flow.get("流出资金"))
        net_inflow = inflow - outflow if inflow is not None and outflow is not None else intraday_number(flow.get("净额"))
        if net_inflow is None:
            continue
        grouped.setdefault((sector_key, label), []).append({
            "net_inflow": net_inflow,
            "change_pct": intraday_number(flow.get("行业-涨跌幅")),
        })
    items: list[dict[str, Any]] = []
    for (sector_key, label), rows in grouped.items():
        net_values = [float(row["net_inflow"]) for row in rows if row["net_inflow"] is not None]
        change_values = [float(row["change_pct"]) for row in rows if row["change_pct"] is not None]
        items.append({
            "taxonomy_key": f"eastmoney_{kind}", "sector_key": sector_key, "label": label,
            "net_inflow": round(median(net_values), 6),
            "change_pct": round(median(change_values), 6) if change_values else None,
        })
    items.sort(key=lambda item: (-float(item["net_inflow"]), str(item["sector_key"])))
    return items


__all__ = ["intraday_board_flow_curve_items"]
