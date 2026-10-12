"""Pure projections and agreement measurements for all-A snapshot sources."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from .datasources.catalog import BINDINGS


_TDX_BINDING = next(item for item in BINDINGS
                    if item.source == "tdx_public" and item.capability == "quote.all_a_snapshot")
_TDX_VOLUME_FACTOR = _TDX_BINDING.spec.unit_factors["volume"]


def project_tdx_snapshot_rows(rows: Iterable[Mapping[str, Any]], observed_at: datetime) -> list[dict[str, Any]]:
    """Project native TDX all-A rows into the Fuyao snapshot row shape."""
    observed = observed_at.isoformat()
    projected: list[dict[str, Any]] = []
    for row in rows:
        price = row["price"]
        if price <= 0:
            continue
        pre_close = row["pre_close"]
        volume = row["volume_lots"] * _TDX_VOLUME_FACTOR
        turnover = row["amount"]
        projected.append({
            "symbol": row["symbol"],
            "price": price,
            "pct_change": row["pct_change"],
            "turnover": turnover,
            "volume": volume,
            "price_source": "tdx_public_all_a_snapshot",
            "price_observed_at": observed,
            "raw": {
                "thscode": row["symbol"],
                "ticker": row["code"],
                "volume": volume,
                "turnover": turnover,
                "last_price": price,
                "price_change": round(price - pre_close, 2),
                "price_change_ratio_pct": row["pct_change"],
                "open_price": row["open"],
                "high_price": row["high"],
                "low_price": row["low"],
                "prev_price": pre_close,
            },
        })
    return projected

__all__ = ["project_tdx_snapshot_rows"]
