"""Database projections for the limit-up risk reading."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from .datasources.http import ashare_symbol
from .derived_daily_readings import session_close
from .market_temperature_repository import UNIT_RATIO

LIST_KEYS = tuple(f"pid:{pid}" for pid in "12345")


def _body(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value)


def _observations(connection: Any, provider: str, capability: str, day: date) -> dict[str, list[list[Any]]]:
    rows = connection.execute(
        """SELECT DISTINCT ON (symbol) symbol,payload FROM quant.raw_market_observations
           WHERE provider_key=%s AND capability=%s AND market='cn' AND effective_at=%s
           ORDER BY symbol,available_at DESC""",
        (provider, capability, session_close(day)),
    ).fetchall()
    result: dict[str, list[list[Any]]] = {}
    for row in rows:
        body = _body(dict(row)["payload"])
        result[str(dict(row)["symbol"])] = list(body.get("rows") or [])
    return result


def read_lists(connection: Any, day: date) -> tuple[dict[str, list[list[Any]]], str]:
    history = _observations(connection, "longhuvip_history", "limit_performance_history", day)
    if any(history.get(key) for key in LIST_KEYS):
        return {key: history.get(key, []) for key in LIST_KEYS}, "history"
    live = _observations(connection, "longhuvip", "limit_performance_live", day)
    return {key: live.get(key, []) for key in LIST_KEYS}, "live"


def _symbols(lists: dict[str, list[list[Any]]]) -> list[str]:
    symbols: list[str] = []
    for key in LIST_KEYS:
        for row in lists[key]:
            if isinstance(row, list) and len(row) >= 23:
                symbol = ashare_symbol(row[0])
                if symbol and not symbol.endswith(".BJ") and symbol not in symbols:
                    symbols.append(symbol)
    return symbols


def read_inputs(connection: Any, day: date) -> dict[str, Any]:
    lists, list_source = read_lists(connection, day)
    symbols = _symbols(lists)
    bars = {}
    for row in connection.execute(
            """SELECT b.symbol,b.open,b.close,b.pre_close,
                       CASE WHEN b.volume > 0 AND b.amount / (b.volume * b.close / 10.0) > %s
                            THEN b.amount / 1000 ELSE b.amount END AS amount,
                       coalesce(nullif(b.limit_up, 0), limits.limit_up) AS limit_up
                 FROM quant.canonical_bars_daily b
                 LEFT JOIN LATERAL (
                     SELECT limit_up FROM quant.daily_trade_limits
                      WHERE symbol=b.symbol AND trading_date=b.trading_date AND limit_up > 0
                      ORDER BY available_at DESC LIMIT 1
                 ) limits ON true
                WHERE b.trading_date=%s AND b.symbol=ANY(%s)
                  AND NOT coalesce(b.is_suspended, false) AND b.pre_close > 0 AND b.open > 0""",
            (UNIT_RATIO, day, symbols),
        ).fetchall():
        bars[str(row["symbol"])] = {**dict(row), "prior_amounts": []}
    prior = connection.execute(
        """SELECT symbol,amount FROM (
                 SELECT symbol,
                        CASE WHEN volume > 0 AND amount / (volume * close / 10.0) > %s
                             THEN amount / 1000 ELSE amount END AS amount,
                        row_number() OVER (PARTITION BY symbol ORDER BY trading_date DESC) AS position
                   FROM quant.canonical_bars_daily
                  WHERE symbol=ANY(%s) AND trading_date BETWEEN %s AND %s
                    AND NOT coalesce(is_suspended, false) AND pre_close > 0 AND open > 0
             ) recent WHERE position<=20 ORDER BY symbol,position""",
        (UNIT_RATIO, symbols, day - timedelta(days=90), day - timedelta(days=1)),
    ).fetchall()
    for row in prior:
        if str(row["symbol"]) in bars:
            bars[str(row["symbol"])]["prior_amounts"].append(float(row["amount"]))
    return {"lists": lists, "list_source": list_source, "bars": bars}


__all__ = ["LIST_KEYS", "read_inputs", "read_lists"]
