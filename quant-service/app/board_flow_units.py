"""What one board-flow item's net inflow is measured in, and its value in CNY.

The one-minute board-flow snapshot holds two vendors' rows:

- The public rows are 同花顺's own fund-flow pages: akshare's
  ``stock_fund_flow_concept`` / ``_industry`` read data.10jqka.com.cn. This
  was checked against the owner's akshare 1.18.96 on 2026-10-09. They are
  stored under the historical keys ``eastmoney_concept`` /
  ``eastmoney_industry`` and the registered provider key ``eastmoney_free``.
  Their net inflow is 流入资金 - 流出资金, in 100 million CNY.
- Longhu's licensed industry ranking (``longhu_ths_industry``) is in CNY.

The snapshot declared one unit for all of them, so any reader that trusted it
was out by 1e8 on every Longhu row. Items now carry their own unit. For an
item stored before that, its taxonomy says which unit it was.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

UNIT_SCALE = {"cny": 1.0, "100m_cny": 100_000_000.0}
#: The unit an item without its own ``unit`` was stored in, by taxonomy.
TAXONOMY_UNITS = {"longhu_ths_industry": "cny", "eastmoney_concept": "100m_cny", "eastmoney_industry": "100m_cny"}
#: Where each provider key's board flow really comes from.
UPSTREAMS = {"eastmoney_free": "同花顺 data.10jqka.com.cn（akshare stock_fund_flow_*）",
             "longhuvip": "开盘啦授权行业排行"}


def item_unit(item: Mapping[str, Any], payload_unit: str | None = None) -> str | None:
    unit = item.get("unit") or TAXONOMY_UNITS.get(str(item.get("taxonomy_key") or "")) or payload_unit
    return unit if unit in UNIT_SCALE else None


def net_inflow_cny(item: Mapping[str, Any], payload_unit: str | None = None) -> float | None:
    value, unit = item.get("net_inflow"), item_unit(item, payload_unit)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or unit is None:
        return None
    return float(value) * UNIT_SCALE[unit]


__all__ = ["TAXONOMY_UNITS", "UNIT_SCALE", "UPSTREAMS", "item_unit", "net_inflow_cny"]
