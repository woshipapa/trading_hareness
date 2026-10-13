"""Pure projections and agreement measurements for all-A snapshot sources."""

from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any, Iterable, Mapping

from .datasources.catalog import BINDINGS


_TDX_BINDING = next(item for item in BINDINGS
                    if item.source == "tdx_public" and item.capability == "quote.all_a_snapshot")
_TDX_VOLUME_FACTOR = _TDX_BINDING.spec.unit_factors["volume"]
TDX_SNAPSHOT_MIN_COMPLETE_SHARE = 0.95
TDX_SNAPSHOT_ROSTER_FLOOR = 5000


def tdx_snapshot_completeness(priced_rows: int, warnings: Iterable[str],
                              roster_total: int | None) -> dict[str, Any]:
    no_trade_rows = next(
        (int(warning.removeprefix("no_trade_rows="))
         for warning in warnings if warning.startswith("no_trade_rows=")),
        0,
    )
    roster = max(priced_rows + no_trade_rows, roster_total or 0, TDX_SNAPSHOT_ROSTER_FLOOR)
    share = priced_rows / roster
    return {
        "priced_rows": priced_rows,
        "no_trade_rows": no_trade_rows,
        "roster_total": roster_total,
        "roster": roster,
        "share": share,
        "complete": roster_total is not None and share >= TDX_SNAPSHOT_MIN_COMPLETE_SHARE,
    }


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

def _share(values: Iterable[bool]) -> float | None:
    values = list(values)
    return sum(values) / len(values) if values else None


def _ratio_values(rows: Iterable[tuple[float, float]]) -> list[float]:
    return [right / left for left, right in rows if left != 0]


def compare(fuyao_rows: Iterable[Mapping[str, Any]], tdx_rows: Iterable[Mapping[str, Any]],
            seconds_apart: float) -> dict[str, Any]:
    """Measure the agreement of two all-A snapshots without applying a verdict."""
    fuyao = {row["symbol"]: row for row in fuyao_rows}
    tdx = {row["symbol"]: row for row in tdx_rows}
    common = sorted(fuyao.keys() & tdx.keys())
    pairs = [(fuyao[symbol], tdx[symbol]) for symbol in common]

    price_gaps = [(abs(tdx_row["price"] - fuyao_row["price"]),
                   abs(tdx_row["price"] - fuyao_row["price"]) / abs(fuyao_row["price"]), symbol)
                  for symbol, (fuyao_row, tdx_row) in zip(common, pairs)]
    pct_gaps = [abs(tdx_row["pct_change"] - fuyao_row["pct_change"])
                for fuyao_row, tdx_row in pairs]
    volume_ratios = _ratio_values((fuyao_row["volume"], tdx_row["volume"])
                                  for fuyao_row, tdx_row in pairs)
    turnover_ratios = _ratio_values((fuyao_row["turnover"], tdx_row["turnover"])
                                    for fuyao_row, tdx_row in pairs)
    largest_price_gap = max(price_gaps, default=None, key=lambda item: item[1])

    return {
        "symbols_in_both": len(common),
        "only_in_fuyao": sorted(fuyao.keys() - tdx.keys()),
        "only_in_tdx": sorted(tdx.keys() - fuyao.keys()),
        "price": {
            "within_one_tick": _share(gap[0] <= 0.01 for gap in price_gaps),
            "within_0_1_pct": _share(gap[1] <= 0.001 for gap in price_gaps),
            "within_0_5_pct": _share(gap[1] <= 0.005 for gap in price_gaps),
            "largest_relative_gap": largest_price_gap[1] if largest_price_gap else None,
            "largest_relative_gap_symbol": largest_price_gap[2] if largest_price_gap else None,
        },
        "pct_change": {"largest_absolute_gap": max(pct_gaps, default=None)},
        "volume": {
            "median_ratio": median(volume_ratios) if volume_ratios else None,
            "within_1_pct": _share(abs(ratio - 1) <= 0.01 for ratio in volume_ratios),
        },
        "turnover": {
            "median_ratio": median(turnover_ratios) if turnover_ratios else None,
            "within_1_pct": _share(abs(ratio - 1) <= 0.01 for ratio in turnover_ratios),
        },
        "seconds_apart": seconds_apart,
    }


__all__ = [
    "TDX_SNAPSHOT_MIN_COMPLETE_SHARE", "TDX_SNAPSHOT_ROSTER_FLOOR", "compare",
    "project_tdx_snapshot_rows", "tdx_snapshot_completeness",
]
