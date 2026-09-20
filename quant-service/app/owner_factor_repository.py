"""Read the owner's persisted adjustment-factor control cross-section.

The owner close pipeline no longer fetches ``adj_factor`` from Tushare.  The
04:30 owner task derives the cumulative series from Longhu 前复权 K-lines and
stores it before downstream control gates run.  This repository only reads
that evidence (including an explicitly allowed Tushare checkpoint when one is
the selected persisted row); it never derives, repairs, or writes factors.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .adjustment_factor_semantics import COMPLETE_FACTOR_PROVIDERS, persisted_factor_semantics_sql


FACTOR_PROVIDER_ORDER = (
    "longhu_qfq_derived", "tushare", "tushare_primary", "tushare_backup",
    "tushare_super", "tushare_super_get", "tushare_super_sdk",
)


def read_persisted_factor_controls(
    connection: Any, trade_date: date, symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Return one eligible factor row per point-in-time all-A bar.

    The session boundary is intentional: a factor learned after the close of
    its own trading date cannot make that date's control plane appear ready.
    Provider order is authoritative before availability: the owner-derived
    Longhu row wins over a newer peer checkpoint, while availability breaks
    ties within the same provider.
    """
    symbol_clause = ""
    parameters: tuple[Any, ...]
    if symbols is not None:
        symbol_clause = " AND bar.symbol=ANY(%s::text[])"
        parameters = (list(COMPLETE_FACTOR_PROVIDERS), list(FACTOR_PROVIDER_ORDER), trade_date, list(symbols))
    else:
        parameters = (list(COMPLETE_FACTOR_PROVIDERS), list(FACTOR_PROVIDER_ORDER), trade_date)
    factor_semantics_sql = persisted_factor_semantics_sql("item")
    rows = connection.execute(
        f"""SELECT bar.symbol AS ts_code,
                      to_char(bar.trading_date,'YYYYMMDD') AS trade_date,
                      factor.adj_factor,
                      factor.provider AS factor_provider,
                      factor.available_at AS factor_available_at
                 FROM quant.canonical_bars_daily bar
                 JOIN quant.universe_membership_history member
                   ON member.universe_key='all_a'
                  AND member.symbol=bar.symbol
                  AND member.effective_from<=bar.trading_date
                  AND (member.effective_to IS NULL OR member.effective_to>=bar.trading_date)
                 JOIN LATERAL (
                       SELECT item.adj_factor,item.provider,item.available_at
                         FROM quant.daily_adjustment_factors item
                        WHERE item.symbol=bar.symbol
                          AND item.trading_date=bar.trading_date
                          AND item.provider=ANY(%s::text[])
                          AND {factor_semantics_sql}
                          AND item.adj_factor>0
                          AND item.available_at<((bar.trading_date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
                        ORDER BY array_position(%s::text[],item.provider) NULLS LAST,
                                 item.available_at DESC,
                                 item.provider
                        LIMIT 1
                 ) factor ON TRUE
                WHERE bar.trading_date=%s
                  AND bar.quality_status='fresh'
                  AND bar.available_at<((bar.trading_date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
                  {symbol_clause}
                ORDER BY bar.symbol""",
        parameters,
    ).fetchall()
    normalized = [dict(row) for row in rows]
    return {
        "rows": normalized,
        "providers": sorted({str(row.get("factor_provider") or "") for row in normalized if row.get("factor_provider")}),
        "trade_date": str(trade_date),
        "source": "owner_persisted_adjustment_factor",
    }


def read_persisted_factor_window(
    connection: Any, symbol: str, start_date: date, end_date: date,
) -> list[dict[str, Any]]:
    """Read an eligible single-symbol factor window for on-demand research."""
    factor_semantics_sql = persisted_factor_semantics_sql("factor")
    rows = connection.execute(
        f"""SELECT factor.symbol,factor.trading_date,factor.adj_factor,
                      factor.provider AS factor_provider,
                      factor.raw->>'factor_semantics' AS factor_semantics,
                      CASE WHEN {factor_semantics_sql} THEN 'complete' ELSE 'pending' END AS adjustment_state,
                      factor.available_at
                 FROM quant.daily_adjustment_factors factor
                WHERE factor.symbol=%s AND factor.trading_date BETWEEN %s AND %s
                  AND factor.provider=ANY(%s::text[])
                  AND {factor_semantics_sql}
                  AND factor.adj_factor>0
                  AND factor.available_at<((factor.trading_date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')
                ORDER BY factor.trading_date,
                         array_position(%s::text[],factor.provider) NULLS LAST,
                         factor.available_at DESC,factor.provider""",
        (symbol, start_date, end_date, list(COMPLETE_FACTOR_PROVIDERS), list(FACTOR_PROVIDER_ORDER)),
    ).fetchall()
    # The table's primary key is provider-specific, so multiple checkpoints
    # can otherwise leak into the on-demand projection. Keep the first row
    # chosen by the provider/availability SQL order for each session.
    selected: dict[Any, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        selected.setdefault(item.get("trading_date"), item)
    return list(selected.values())


__all__ = ["FACTOR_PROVIDER_ORDER", "read_persisted_factor_controls", "read_persisted_factor_window"]
