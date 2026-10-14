"""605577.SH: the sessions of 2026 without a stored daily bar, and how many stocks have a bar on each of them.
Read-only. Run inside the owner's scheduler container, stdin = this file."""
from app.database import Database

SYMBOL = "605577.SH"
database = Database()
database.open()
with database.transaction() as connection:
    connection.execute("SET LOCAL statement_timeout = '25s'")
    missing = [row["calendar_date"] for row in connection.execute(
        """SELECT c.calendar_date FROM quant.market_trade_calendar c
            WHERE c.exchange='SSE' AND c.is_open AND c.calendar_date BETWEEN '2026-01-01' AND current_date - 1
              AND NOT EXISTS (SELECT 1 FROM quant.canonical_bars_daily b WHERE b.symbol=%s AND b.trading_date=c.calendar_date)
            ORDER BY 1""", (SYMBOL,)).fetchall()]
    print("sessions of 2026 without a bar:", [str(day) for day in missing])
    for day in missing:
        row = connection.execute(
            """SELECT count(*) AS bars, count(adj_factor) AS with_factor,
                      count(*) FILTER (WHERE selected_provider LIKE 'longhu%%') AS from_longhu,
                      count(*) FILTER (WHERE selected_provider LIKE 'tushare%%') AS from_tushare
                 FROM quant.canonical_bars_daily WHERE trading_date=%s""", (day,)).fetchone()
        around = connection.execute(
            """SELECT trading_date, close, pre_close, selected_provider FROM quant.canonical_bars_daily
                WHERE symbol=%s AND trading_date BETWEEN %s::date - 4 AND %s::date + 4 ORDER BY 1""", (SYMBOL, day, day)).fetchall()
        print(f"  {day}: whole market {dict(row)}")
        print("     neighbours:", [(str(item["trading_date"]), str(item["close"]), str(item["pre_close"]), item["selected_provider"]) for item in around])
    no_limit = connection.execute(
        "SELECT trading_date FROM quant.canonical_bars_daily WHERE symbol=%s AND trading_date >= '2026-01-01' AND limit_up IS NULL ORDER BY 1", (SYMBOL,)).fetchall()
    print("bars of 2026 without limit_up:", [str(row["trading_date"]) for row in no_limit])
    print("bars of 2023 without adj_factor: whole market by month (bars, with factor)")
    for row in connection.execute(
            """SELECT to_char(trading_date, 'YYYY-MM') AS month, count(*) AS bars, count(adj_factor) AS with_factor
                 FROM quant.canonical_bars_daily WHERE trading_date BETWEEN '2023-08-01' AND '2023-12-31' GROUP BY 1 ORDER BY 1""").fetchall():
        print("  ", dict(row))
