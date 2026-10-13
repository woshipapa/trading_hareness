"""Bounded range reads for tactic-trial research evidence."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from .datasources.http import ashare_symbol
from .derived_daily_readings import newest, session_close

CN_TZ = ZoneInfo("Asia/Shanghai")
CHUNK_SESSIONS = 60
LIST_KEYS = tuple(f"pid:{pid}" for pid in "12345")
READING_CAPABILITIES = (
    "tactic_trials_close_daily", "tactic_trials_auction_daily", "tactic_trials_settled_daily",
    "tactic_trials_summary_daily", "limit_up_risk_daily", "auction_open_desk_daily",
)


def _body(value: Any) -> dict[str, Any]:
    return json.loads(value) if isinstance(value, str) else dict(value)


def read_limit_lists(connection: Any, start: date, end: date) -> dict[str, Any]:
    """Read both stored limit-list sources, preferring history per day when present."""
    rows = connection.execute(
        """SELECT provider_key,capability,symbol,effective_at,payload
             FROM quant.raw_market_observations
            WHERE provider_key IN ('longhuvip_history','longhuvip')
              AND capability IN ('limit_performance_history','limit_performance_live')
              AND market='cn' AND effective_at BETWEEN %s AND %s
            ORDER BY effective_at,symbol,available_at DESC""",
        (session_close(start), session_close(end)),
    ).fetchall()
    grouped: dict[date, dict[str, dict[str, list[list[Any]]]]] = {}
    for item in rows:
        row = dict(item)
        day = row["effective_at"].astimezone(CN_TZ).date()
        body = _body(row["payload"])
        provider = "history" if row["provider_key"] == "longhuvip_history" else "live"
        grouped.setdefault(day, {}).setdefault(provider, {})[str(row["symbol"])] = list(body.get("rows") or body.get("list") or [])
    result = []
    for day in sorted(grouped):
        by_source = grouped[day]
        source = "history" if any(by_source.get("history", {}).get(key) for key in LIST_KEYS) else "live"
        result.append({"trade_date": day, "lists": {key: by_source.get(source, {}).get(key, []) for key in LIST_KEYS}, "source": source})
    return {"days": result}


def read_day_bars(connection: Any, start: date, end: date, symbols: Sequence[str]) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(
        """SELECT b.symbol,b.trading_date,b.open,b.high,b.low,b.close,b.pre_close,b.amount,
                      coalesce(nullif(b.limit_up,0),limits.limit_up) AS limit_up,
                      coalesce(nullif(b.limit_down,0),limits.limit_down) AS limit_down,b.adj_factor
                 FROM quant.canonical_bars_daily b
                 LEFT JOIN LATERAL (SELECT limit_up,limit_down FROM quant.daily_trade_limits
                                      WHERE symbol=b.symbol AND trading_date=b.trading_date AND limit_up>0
                                      ORDER BY available_at DESC LIMIT 1) limits ON true
                WHERE b.trading_date BETWEEN %s AND %s AND b.symbol=ANY(%s)""",
        (start, end, list(symbols)),
    ).fetchall()]


def read_history_windows(connection: Any, symbol_days: Sequence[tuple[str, date]]) -> list[dict[str, Any]]:
    if not symbol_days:
        return []
    symbols, days = zip(*symbol_days)
    return [dict(row) for row in connection.execute(
        """WITH wanted(symbol,pick_day) AS (SELECT * FROM unnest(%s::text[],%s::date[]))
           SELECT w.symbol,w.pick_day,b.trading_date,b.close,b.high,b.volume,b.amount,b.adj_factor
             FROM wanted w JOIN quant.canonical_bars_daily b ON b.symbol=w.symbol
            WHERE b.trading_date BETWEEN w.pick_day - 400 AND w.pick_day
            ORDER BY w.symbol,w.pick_day,b.trading_date""",
        (list(symbols), list(days)),
    ).fetchall()]


def read_market_turnover(connection: Any, start: date, end: date) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(
        """SELECT trading_date,sum(amount)*1000 AS market_turnover
             FROM quant.canonical_bars_daily
            WHERE trading_date BETWEEN %s AND %s AND NOT coalesce(is_suspended,false)
            GROUP BY trading_date ORDER BY trading_date""", (start, end),
    ).fetchall()]


def read_exit_bars(connection: Any, symbol_days: Sequence[tuple[str, date]]) -> list[dict[str, Any]]:
    if not symbol_days:
        return []
    symbols, days = zip(*symbol_days)
    return [dict(row) for row in connection.execute(
        """WITH wanted(symbol,pick_day) AS (SELECT * FROM unnest(%s::text[],%s::date[]))
           SELECT w.symbol,w.pick_day,b.trading_date,b.open,b.high,b.low,b.close,b.pre_close,b.adj_factor,
                  b.is_suspended,limits.limit_up,limits.limit_down
             FROM wanted w JOIN quant.canonical_bars_daily b ON b.symbol=w.symbol
             LEFT JOIN LATERAL (SELECT limit_up,limit_down FROM quant.daily_trade_limits
                                  WHERE symbol=b.symbol AND trading_date=b.trading_date AND limit_up>0
                                  ORDER BY available_at DESC LIMIT 1) limits ON true
            WHERE b.trading_date BETWEEN w.pick_day-30 AND w.pick_day+30
            ORDER BY w.symbol,w.pick_day,b.trading_date""",
        (list(symbols), list(days)),
    ).fetchall()]


def read_capability(connection: Any, capability: str, start: date, end: date) -> list[dict[str, Any]]:
    return newest(connection, capability, start, end)


def read_capabilities(connection: Any, start: date, end: date) -> dict[str, list[dict[str, Any]]]:
    return {capability: read_capability(connection, capability, start, end) for capability in READING_CAPABILITIES}


def read_auction_lists(connection: Any, start: date, end: date) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT effective_at,symbol,payload FROM quant.raw_market_observations
            WHERE provider_key='longhuvip_history' AND capability='morning_bidding_history'
              AND market='cn' AND effective_at BETWEEN %s AND %s
            ORDER BY effective_at,available_at DESC,symbol""",
        (session_close(start), session_close(end)),
    ).fetchall()
    grouped: dict[date, list[dict[str, Any]]] = {}
    for item in rows:
        row = dict(item)
        day = row["effective_at"].astimezone(CN_TZ).date()
        payload = _body(row["payload"])
        source_rows = payload.get("rows") or payload.get("list") or []
        grouped.setdefault(day, []).extend(
            {"symbol": ashare_symbol(source[0]) if isinstance(source, (list, tuple)) and source else None,
             "amount": source[4] if isinstance(source, (list, tuple)) and len(source) > 4 else None,
             "position": position}
            for position, source in enumerate(source_rows)
        )
    return [{"trade_date": day, "rows": rows} for day, rows in sorted(grouped.items())]


def read_open_sessions(connection: Any, start: date, end: date) -> list[date]:
    today = datetime.now(CN_TZ).date()
    rows = connection.execute(
        """SELECT calendar_date FROM quant.market_trade_calendar
            WHERE exchange='SSE' AND is_open AND calendar_date BETWEEN %s AND %s AND calendar_date<=%s
            ORDER BY calendar_date""", (start, end, today),
    ).fetchall()
    return [row["calendar_date"] for row in rows]


def read_next_sessions(connection: Any, day: date, count: int) -> list[date]:
    today = datetime.now(CN_TZ).date()
    rows = connection.execute(
        """SELECT calendar_date FROM quant.market_trade_calendar
            WHERE exchange='SSE' AND is_open AND calendar_date>%s AND calendar_date<=%s
            ORDER BY calendar_date LIMIT %s""", (day, today, count),
    ).fetchall()
    return [row["calendar_date"] for row in rows]


__all__ = [
    "CHUNK_SESSIONS", "READING_CAPABILITIES", "read_auction_lists", "read_capabilities", "read_capability",
    "read_day_bars", "read_exit_bars", "read_history_windows", "read_limit_lists", "read_market_turnover",
    "read_next_sessions", "read_open_sessions",
]
