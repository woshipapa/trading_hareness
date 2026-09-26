"""Database reads that feed the pure T+1 settlement rule.

Callers own the transaction and their outcome table; this module supplies the
forward bars of one idea and the equal-weight market series the benchmark is
built from, so the three settlement lines stop carrying their own SQL.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from .t1_settlement import MAX_EXIT_ROLL_SESSIONS, SETTLEMENT_VERSION, market_window_return, settle

#: Listed A-share equities; the same families the universe and controls use.
A_SHARE_EQUITY_PATTERN = (
    r"^(?:(?:60[0135]|68[89])[0-9]{3}\.SH|(?:000|001|002|003|300|301|302)[0-9]{3}\.SZ|[489][0-9]{5}\.BJ)$"
)
#: A single-session move beyond the widest limit band is a no-limit listing
#: day (or a data fault); it would dominate an equal-weight mean.
MAX_BENCHMARK_DAILY_MOVE = Decimal("0.31")


def forward_bars(connection: Any, symbol: str, signal_date: date, as_of_date: date,
                 horizon_sessions: int) -> list[dict[str, Any]]:
    """The sessions after ``signal_date`` an outcome may need, oldest first."""
    rows = connection.execute(
        """SELECT trading_date,open,high,low,close,pre_close,adj_factor,is_suspended,limit_up,limit_down
             FROM quant.canonical_bars_daily
            WHERE symbol=%s AND trading_date>%s AND trading_date<=%s
            ORDER BY trading_date LIMIT %s""",
        (symbol, signal_date, as_of_date, max(int(horizon_sessions), 2) + MAX_EXIT_ROLL_SESSIONS),
    ).fetchall()
    return [dict(row) for row in rows]


class EqualWeightMarket:
    """Per-session equal-weight A-share returns, loaded once per settle run."""

    def __init__(self, connection: Any, start: date, end: date):
        rows = connection.execute(
            """SELECT trading_date,
                      avg(close/open-1) FILTER (WHERE open>0 AND abs(close/open-1)<=%s) AS open_to_close,
                      avg(close/pre_close-1) FILTER (WHERE pre_close>0 AND abs(close/pre_close-1)<=%s) AS close_to_close
                 FROM quant.canonical_bars_daily
                WHERE trading_date BETWEEN %s AND %s AND NOT is_suspended AND symbol ~ %s
                GROUP BY trading_date""",
            (MAX_BENCHMARK_DAILY_MOVE, MAX_BENCHMARK_DAILY_MOVE, start, end, A_SHARE_EQUITY_PATTERN),
        ).fetchall()
        self.daily = {
            row["trading_date"]: {
                "open_to_close": Decimal(str(row["open_to_close"])) if row["open_to_close"] is not None else None,
                "close_to_close": Decimal(str(row["close_to_close"])) if row["close_to_close"] is not None else None,
            }
            for row in (dict(item) for item in rows)
        }

    def window_return(self, entry_date: date, exit_date: date) -> Decimal | None:
        return market_window_return(self.daily, entry_date, exit_date)


def settle_idea(connection: Any, *, symbol: str, signal_date: date, as_of_date: date, direction: int,
                horizon_sessions: int, market: EqualWeightMarket | None) -> dict[str, Any]:
    """Load one idea's forward bars and settle it."""
    bars = forward_bars(connection, symbol, signal_date, as_of_date, horizon_sessions)
    return settle(bars, symbol=symbol, direction=direction, horizon_sessions=horizon_sessions,
                  benchmark_return=market.window_return if market else None)


def settlement_values(result: dict[str, Any]) -> tuple[Any, ...]:
    """The settlement columns every outcome table shares, in one fixed order.

    Matches ``SETTLEMENT_COLUMNS``; callers splice it into their own INSERT.
    """
    return (
        result["entry_date"], result["exit_date"], result["entry_price"], result["exit_price"],
        result["gross_return"], result["net_return"], result["benchmark_return"], result["benchmark_key"],
        result["excess_return"], result["maximum_favorable_excursion"], result["maximum_adverse_excursion"],
        result["tradability"], result["sessions_held"], result["exit_rolled_sessions"], result["direction"],
        result["price_basis"], result["settlement_version"],
    )


SETTLEMENT_COLUMNS = (
    "entry_date", "exit_date", "{entry_price}", "{exit_price}", "raw_return", "net_return", "benchmark_return",
    "benchmark_key", "excess_return", "maximum_favorable_excursion", "maximum_adverse_excursion", "tradability",
    "sessions_held", "exit_rolled_sessions", "direction", "price_basis", "settlement_version",
)


def settlement_columns(entry_price_column: str, exit_price_column: str) -> list[str]:
    return [column.format(entry_price=entry_price_column, exit_price=exit_price_column) for column in SETTLEMENT_COLUMNS]


def settlement_update_sql(entry_price_column: str, exit_price_column: str, *, keys: tuple[str, ...]) -> str:
    """``SET`` list refreshing every settlement column except the conflict keys."""
    columns = [column for column in settlement_columns(entry_price_column, exit_price_column) if column not in keys]
    return ",".join(f"{column}=EXCLUDED.{column}" for column in columns) + ",calculated_at=now()"


__all__ = [
    "A_SHARE_EQUITY_PATTERN", "EqualWeightMarket", "SETTLEMENT_COLUMNS", "SETTLEMENT_VERSION", "forward_bars",
    "settle_idea", "settlement_columns", "settlement_update_sql", "settlement_values",
]
