"""Database projections for the limit-up risk reading."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from .derived_daily_readings import session_close
from .limit_up_risk import symbol_for_code

LIST_KEYS = tuple(f"pid:{pid}" for pid in "12345")


def _body(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value)


def _observations(connection: Any, provider: str, capability: str, day: date) -> dict[str, list[list[Any]]]:
    rows = connection.execute(
        """SELECT symbol,payload FROM quant.raw_market_observations
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
                code = str(row[0]).zfill(6)
                if not code.startswith(("4", "8")):
                    symbol = symbol_for_code(code)
                    if symbol not in symbols:
                        symbols.append(symbol)
    return symbols


def read_inputs(connection: Any, day: date) -> dict[str, Any]:
    lists, list_source = read_lists(connection, day)
    symbols = _symbols(lists)
    bars = {
        str(row["symbol"]): dict(row)
        for row in connection.execute(
            """SELECT symbol,open,close,pre_close,amount,limit_up
                 FROM quant.canonical_bars_daily WHERE trading_date=%s AND symbol=ANY(%s)""",
            (day, symbols),
        ).fetchall()
    }
    prior = connection.execute(
        """SELECT symbol,amount FROM (
                 SELECT symbol,amount,row_number() OVER (PARTITION BY symbol ORDER BY trading_date DESC) AS position
                   FROM quant.canonical_bars_daily
                  WHERE symbol=ANY(%s) AND trading_date<%s
             ) recent WHERE position<=20 ORDER BY symbol,position""",
        (symbols, day),
    ).fetchall()
    for row in prior:
        bars.setdefault(str(row["symbol"]), {}).setdefault("prior_amounts", []).append(float(row["amount"]))
    limits = {
        str(row["symbol"]): float(row["limit_up"])
        for row in connection.execute(
            """SELECT DISTINCT ON(symbol) symbol,limit_up FROM quant.daily_trade_limits
                 WHERE trading_date=%s AND limit_up>0 ORDER BY symbol,available_at DESC""",
            (day,),
        ).fetchall()
    }
    return {"lists": lists, "list_source": list_source, "bars": bars, "limits": limits}


__all__ = ["LIST_KEYS", "read_inputs", "read_lists"]
