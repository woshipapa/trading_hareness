"""龙版传媒 605577.SH on the owner database: which daily bars and adjustment factors are stored, which are missing.
Read-only; counts, dates and a few recent rows. Run inside the owner's scheduler container, stdin = this file."""
from app.database import Database

SYMBOL = "605577.SH"
database = Database()
database.open()
with database.transaction() as connection:
    connection.execute("SET LOCAL statement_timeout = '25s'")
    print("now (database):", connection.execute("SELECT now() AT TIME ZONE 'Asia/Shanghai' AS t").fetchone()["t"])
    for row in connection.execute("SELECT * FROM quant.instruments WHERE symbol=%s", (SYMBOL,)).fetchall():
        print("instrument:", {key: str(value)[:40] for key, value in dict(row).items() if value is not None})
    print("universe:", [(row["universe_key"], row["enabled"]) for row in connection.execute(
        "SELECT universe_key, enabled FROM quant.universe_members WHERE symbol=%s", (SYMBOL,)).fetchall()])
    print("bars by year: sessions of the calendar, bars stored, bars with adj_factor, bars with limit_up, suspended")
    for row in connection.execute(
            """SELECT extract(year FROM c.calendar_date)::int AS year, count(*) AS sessions, count(b.symbol) AS bars,
                      count(b.adj_factor) AS with_factor, count(b.limit_up) AS with_limit, count(*) FILTER (WHERE b.is_suspended) AS suspended,
                      min(c.calendar_date) FILTER (WHERE b.symbol IS NULL) AS first_missing,
                      max(c.calendar_date) FILTER (WHERE b.symbol IS NULL) AS last_missing,
                      min(b.trading_date) FILTER (WHERE b.adj_factor IS NULL) AS first_no_factor,
                      max(b.trading_date) FILTER (WHERE b.adj_factor IS NULL) AS last_no_factor
                 FROM quant.market_trade_calendar c
                 LEFT JOIN quant.canonical_bars_daily b ON b.symbol=%s AND b.trading_date=c.calendar_date
                WHERE c.exchange='SSE' AND c.is_open AND c.calendar_date BETWEEN '2023-08-15' AND current_date
                GROUP BY 1 ORDER BY 1""", (SYMBOL,)).fetchall():
        print("  ", dict(row))
    print("factor table by provider:")
    for row in connection.execute(
            """SELECT provider, count(*) AS rows, min(trading_date) AS first, max(trading_date) AS last
                 FROM quant.daily_adjustment_factors WHERE symbol=%s GROUP BY 1 ORDER BY 1""", (SYMBOL,)).fetchall():
        print("  ", dict(row))
    print("last 12 bars:")
    for row in connection.execute(
            """SELECT trading_date, close, pre_close, adj_factor, limit_up, is_suspended, quality_status, selected_provider
                 FROM quant.canonical_bars_daily WHERE symbol=%s ORDER BY trading_date DESC LIMIT 12""", (SYMBOL,)).fetchall():
        print("  ", {key: str(value) for key, value in dict(row).items()})
    print("whole market on the last 3 sessions: bars and bars with adj_factor")
    for row in connection.execute(
            """SELECT trading_date, count(*) AS bars, count(adj_factor) AS with_factor
                 FROM quant.canonical_bars_daily WHERE trading_date >= current_date - 6 GROUP BY 1 ORDER BY 1 DESC LIMIT 3""").fetchall():
        print("  ", dict(row))
