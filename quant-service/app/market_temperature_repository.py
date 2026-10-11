"""Daily aggregates for the sentiment temperature, computed in PostgreSQL from all-A daily bars.

One row per session:
- limit-up, touched and limit-down counts against the session's own limit
  prices;
- advancers and decliners;
- turnover, with rows whose amount is in CNY instead of thousand CNY scaled
  back (see ``UNIT_RATIO``);
- yesterday's limit-ups: how many there were, how many sealed again today, and
  their mean return today (the money effect);
- the highest consecutive limit-up streak.

The members are the point-in-time all-A universe, suspended rows excluded.

Two sessions of daily bars (2026-08-27, 2026-09-29) carry no limit prices.
For a session like that, the limits come from ``quant.daily_trade_limits``,
newest provider row first. A session still without them is flagged
``limits_ok=false``, so its limit components are unknown rather than zero.

The whole history (about 430 sessions) takes about 13 s on the owner database,
so the result is computed once after the close and read back; the request path
never rescans the bars.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

INDEX_SYMBOL = "000001.SH"
LIMIT_TOLERANCE = 0.005          # prices are in cents; a close within half a cent of the limit is at it
STREAK_WARMUP_DAYS = 40          # calendar days read before ``start`` so streaks and yesterday's set are whole
LIMITS_OK_SHARE = 0.9            # a session counts as having limit prices when 90% of its rows do
#: amount (thousand CNY) / (volume (lots) x close / 10) is about 1 for a sound row. 408 rows from
#: legacy:tencent, legacy:longhuvip:GetStockPanKou and akshare (2026-07 to 2026-09) are about 1000:
#: amounts in CNY. A handful a day lifted the all-A total to 25 trillion; they are scaled back here.
UNIT_RATIO = 50

#: Sessions, limit fallback, unit guard, flags and streaks: shared by the daily aggregate and the intraday prior set.
STREAK_CTES = """
WITH sessions AS (
  SELECT trading_date, lag(trading_date) OVER (ORDER BY trading_date) AS prev_day
    FROM (SELECT DISTINCT trading_date FROM quant.canonical_bars_daily
           WHERE symbol=%(index)s AND trading_date BETWEEN %(warmup)s AND %(end)s) d),
fallback AS (
  SELECT DISTINCT ON (symbol, trading_date) symbol, trading_date, limit_up, limit_down
    FROM quant.daily_trade_limits
   WHERE trading_date BETWEEN %(warmup)s AND %(end)s AND limit_up > 0
   ORDER BY symbol, trading_date, available_at DESC),
bars AS (
  SELECT b.symbol, b.trading_date, b.high, b.close, b.pre_close,
         CASE WHEN b.volume > 0 AND b.amount / (b.volume * b.close / 10.0) > %(unit_ratio)s
              THEN b.amount / 1000 ELSE b.amount END AS amount,
         coalesce(nullif(b.limit_up, 0), f.limit_up) AS limit_up,
         coalesce(nullif(b.limit_down, 0), f.limit_down) AS limit_down
    FROM quant.canonical_bars_daily b
    JOIN quant.universe_membership_history m
      ON m.symbol=b.symbol AND m.universe_key='all_a' AND m.effective_from<=b.trading_date
     AND (m.effective_to IS NULL OR m.effective_to>b.trading_date)
    LEFT JOIN fallback f
      ON f.symbol=b.symbol AND f.trading_date=b.trading_date AND coalesce(b.limit_up, 0)=0
   WHERE b.trading_date BETWEEN %(warmup)s AND %(end)s AND NOT coalesce(b.is_suspended, false)
     AND b.pre_close > 0 AND b.close > 0),
flags AS (
  SELECT bars.*, s.prev_day,
         coalesce(limit_up > 0 AND close >= limit_up - %(tol)s, false) AS sealed,
         coalesce(limit_up > 0 AND high >= limit_up - %(tol)s, false) AS touched,
         coalesce(limit_down > 0 AND close <= limit_down + %(tol)s, false) AS floored,
         close / pre_close - 1 AS ret
    FROM bars JOIN sessions s USING (trading_date)),
lagged AS (
  SELECT flags.*, lag(sealed) OVER w AS prev_row_sealed, lag(trading_date) OVER w AS prev_row_day,
         sum(CASE WHEN sealed THEN 0 ELSE 1 END) OVER w AS breaks
    FROM flags WINDOW w AS (PARTITION BY symbol ORDER BY trading_date)),
streaks AS (
  SELECT lagged.*,
         coalesce(prev_row_sealed AND prev_row_day = prev_day, false) AS prev_sealed,
         CASE WHEN sealed THEN count(*) FILTER (WHERE sealed)
                               OVER (PARTITION BY symbol, breaks ORDER BY trading_date)
              ELSE 0 END AS streak
    FROM lagged)
"""

DAILY_SQL = STREAK_CTES + """
SELECT s.trading_date,
       count(*) AS stocks,
       count(*) FILTER (WHERE limit_up > 0) AS with_limits,
       count(*) FILTER (WHERE sealed) AS limit_up,
       count(*) FILTER (WHERE touched) AS touched,
       count(*) FILTER (WHERE floored) AS limit_down,
       count(*) FILTER (WHERE ret > 0) AS advancers,
       count(*) FILTER (WHERE ret < 0) AS decliners,
       sum(amount) * 1000 AS turnover_cny,
       count(*) FILTER (WHERE prev_sealed) AS prev_sealed,
       count(*) FILTER (WHERE prev_sealed AND sealed) AS promoted,
       avg(ret) FILTER (WHERE prev_sealed) * 100 AS premium_pct,
       max(streak) AS max_streak,
       (SELECT close FROM quant.canonical_bars_daily i WHERE i.symbol=%(index)s AND i.trading_date=s.trading_date) AS index_close
  FROM streaks s
 WHERE s.trading_date BETWEEN %(start)s AND %(end)s
 GROUP BY s.trading_date
 ORDER BY s.trading_date
"""


#: The previous session's sealed stocks and their streaks: what the intraday reading compares today with.
PRIOR_SEALED_SQL = STREAK_CTES + """
SELECT symbol, streak::int AS streak FROM streaks WHERE trading_date = %(end)s AND sealed
"""

PREVIOUS_SESSION_SQL = """SELECT max(trading_date) AS day FROM quant.canonical_bars_daily
                           WHERE symbol=%s AND trading_date < %s"""

#: The session's limit prices, stored before its first intraday scan (decision 0005), newest provider row first.
SESSION_LIMITS_SQL = """SELECT DISTINCT ON (symbol) symbol, limit_up, limit_down FROM quant.daily_trade_limits
                         WHERE trading_date=%s AND limit_up > 0 ORDER BY symbol, available_at DESC"""


def _parameters(start: date, end: date, index_symbol: str) -> dict[str, Any]:
    return {"start": start, "end": end, "warmup": start - timedelta(days=STREAK_WARMUP_DAYS),
            "index": index_symbol, "tol": LIMIT_TOLERANCE, "unit_ratio": UNIT_RATIO}


def daily_rows(connection: Any, start: date, end: date, *, index_symbol: str = INDEX_SYMBOL) -> list[dict[str, Any]]:
    """One aggregate row per session in ``[start, end]``; ``turnover_cny`` is in CNY (bars store thousand CNY)."""
    rows = connection.execute(DAILY_SQL, _parameters(start, end, index_symbol)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["limits_ok"] = int(item["with_limits"] or 0) >= LIMITS_OK_SHARE * int(item["stocks"] or 0) > 0
        result.append(item)
    return result


def sessions_between(connection: Any, start: date, end: date, *, index_symbol: str = INDEX_SYMBOL) -> list[date]:
    rows = connection.execute("""SELECT trading_date FROM quant.canonical_bars_daily
                                  WHERE symbol=%s AND trading_date BETWEEN %s AND %s ORDER BY trading_date""",
                              (index_symbol, start, end)).fetchall()
    return [dict(row)["trading_date"] for row in rows]


def previous_session(connection: Any, day: date, *, index_symbol: str = INDEX_SYMBOL) -> date | None:
    row = connection.execute(PREVIOUS_SESSION_SQL, (index_symbol, day)).fetchone()
    return dict(row)["day"] if row else None


def prior_streaks(connection: Any, day: date, *, index_symbol: str = INDEX_SYMBOL) -> tuple[date | None, dict[str, int]]:
    """The session before ``day`` and its sealed stocks with their streaks (1 for a first board)."""
    previous = previous_session(connection, day, index_symbol=index_symbol)
    if previous is None:
        return None, {}
    rows = connection.execute(PRIOR_SEALED_SQL, _parameters(previous, previous, index_symbol)).fetchall()
    return previous, {dict(row)["symbol"]: int(dict(row)["streak"]) for row in rows}


def session_limits(connection: Any, day: date) -> dict[str, tuple[float, float | None]]:
    rows = connection.execute(SESSION_LIMITS_SQL, (day,)).fetchall()
    return {item["symbol"]: (float(item["limit_up"]), float(item["limit_down"]) if item["limit_down"] else None)
            for item in (dict(row) for row in rows)}


__all__ = ["DAILY_SQL", "INDEX_SYMBOL", "LIMIT_TOLERANCE", "LIMITS_OK_SHARE", "PRIOR_SEALED_SQL", "STREAK_CTES",
           "UNIT_RATIO", "daily_rows", "previous_session", "prior_streaks", "session_limits", "sessions_between"]
