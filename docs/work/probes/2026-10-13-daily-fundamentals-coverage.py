"""Is "daily fundamentals 5138/5560" a thing of 2026-10-13 or of every day? Coverage per session, read-only, counts only.
Run inside the owner's quant-research container, stdin = this file."""
from app.database import Database

database = Database()
database.open()
with database.transaction() as connection:
    connection.execute("SET LOCAL statement_timeout = '25s'")
    print("now (database):", connection.execute("SELECT now() AT TIME ZONE 'Asia/Shanghai' AS t").fetchone()["t"])
    for row in connection.execute(
            """SELECT bar.trading_date, count(*) AS bars,
                      count(basic.symbol) AS with_fundamentals,
                      count(basic.symbol) FILTER (WHERE basic.available_at < ((bar.trading_date + 1)::timestamp AT TIME ZONE 'Asia/Shanghai')) AS in_time,
                      max(basic.available_at) AT TIME ZONE 'Asia/Shanghai' AS newest,
                      min(basic.available_at) AT TIME ZONE 'Asia/Shanghai' AS oldest
                 FROM quant.canonical_bars_daily bar
                 LEFT JOIN quant.daily_fundamentals basic ON basic.symbol = bar.symbol AND basic.trading_date = bar.trading_date
                WHERE bar.trading_date >= '2026-09-22' AND bar.quality_status = 'fresh'
                GROUP BY 1 ORDER BY 1""").fetchall():
        print(f"  {row['trading_date']} bars {row['bars']:>5} with fundamentals {row['with_fundamentals']:>5} "
              f"(stored before the next day {row['in_time']:>5}) written {row['oldest']} .. {row['newest']}")
    for row in connection.execute(
            """SELECT right(bar.symbol, 2) AS exchange, left(bar.symbol, 3) AS prefix, count(*) AS n
                 FROM quant.canonical_bars_daily bar
                 LEFT JOIN quant.daily_fundamentals basic ON basic.symbol = bar.symbol AND basic.trading_date = bar.trading_date
                WHERE bar.trading_date = '2026-10-13' AND bar.quality_status = 'fresh' AND basic.symbol IS NULL
                GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 12""").fetchall():
        print(f"  without fundamentals on 2026-10-13: {row['exchange']} {row['prefix']}xxx {row['n']}")
