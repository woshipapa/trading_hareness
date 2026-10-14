"""B073: do the watched symbols' minute sessions of 2026-10-14 cover the morning? Read-only, counts only."""
from app.database import Database
database = Database(); database.open()
with database.transaction() as c:
    c.execute("SET LOCAL statement_timeout = '30s'")
    r = c.execute("""SELECT count(*) AS n, count(DISTINCT symbol) AS symbols, min(bar_time::text) AS first, max(bar_time::text) AS last,
                            count(DISTINCT bar_time) AS minutes, min(available_at) AS first_available, max(available_at) AS last_available,
                            array_agg(DISTINCT source_name) AS sources
                       FROM quant.intraday_minute_sessions WHERE trading_date = '2026-10-14'""").fetchone()
    print(dict(r))
    for row in c.execute("""SELECT symbol, count(*) AS n, min(bar_time::text) AS first, max(bar_time::text) AS last,
                                   count(*) FILTER (WHERE bar_time::text BETWEEN '09:30' AND '11:05') AS morning_rows
                              FROM quant.intraday_minute_sessions WHERE trading_date='2026-10-14'
                             GROUP BY 1 ORDER BY 2 DESC LIMIT 5""").fetchall():
        print(dict(row))
