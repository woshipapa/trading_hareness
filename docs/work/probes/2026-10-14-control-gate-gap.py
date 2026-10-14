"""B043: which stocks have a fresh daily bar and no same-day fundamentals, and what has the gate been stopping?
Read-only, counts and at most 30 short rows. Run inside the owner's quant-research container, stdin = this file."""
from app.database import Database

DAYS = ("2026-10-12", "2026-10-13")
database = Database()
database.open()
with database.transaction() as connection:
    connection.execute("SET LOCAL statement_timeout = '25s'")
    print("now (database):", connection.execute("SELECT now() AT TIME ZONE 'Asia/Shanghai' AS t").fetchone()["t"])
    for day in DAYS:
        # The missing ones by exchange, by whether they traded, and by where the bar came from.
        for row in connection.execute(
                """SELECT right(bar.symbol, 2) AS exchange, coalesce(bar.is_suspended, false) AS suspended,
                          (coalesce(bar.volume, 0) > 0) AS traded, bar.selected_provider, count(*) AS n
                     FROM quant.canonical_bars_daily bar
                     LEFT JOIN quant.daily_fundamentals basic ON basic.symbol = bar.symbol AND basic.trading_date = bar.trading_date
                    WHERE bar.trading_date = %s AND bar.quality_status = 'fresh' AND basic.symbol IS NULL
                    GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 20""", (day,)).fetchall():
            print(f"  {day} no fundamentals: {row['exchange']} suspended={row['suspended']} traded={row['traded']} "
                  f"bar from {row['selected_provider']}: {row['n']}")
        # Which providers the fundamentals of the day come from.
        for row in connection.execute(
                "SELECT provider, count(*) AS n FROM quant.daily_fundamentals WHERE trading_date = %s GROUP BY 1 ORDER BY 2 DESC", (day,)).fetchall():
            print(f"  {day} fundamentals from {row['provider']}: {row['n']}")
    # A sample of the Shanghai and Shenzhen ones without fundamentals on the newer day.
    for row in connection.execute(
            """SELECT bar.symbol, bar.volume, bar.close, bar.is_suspended,
                      (SELECT max(trading_date) FROM quant.daily_fundamentals older WHERE older.symbol = bar.symbol) AS last_fundamentals,
                      (SELECT min(trading_date) FROM quant.canonical_bars_daily first WHERE first.symbol = bar.symbol) AS first_bar
                 FROM quant.canonical_bars_daily bar
                 LEFT JOIN quant.daily_fundamentals basic ON basic.symbol = bar.symbol AND basic.trading_date = bar.trading_date
                WHERE bar.trading_date = %s AND bar.quality_status = 'fresh' AND basic.symbol IS NULL AND bar.symbol !~ '\\.BJ$'
                ORDER BY bar.symbol LIMIT 30""", (DAYS[-1],)).fetchall():
        print(f"  sample {row['symbol']} volume={row['volume']} close={row['close']} suspended={row['is_suspended']} "
              f"last fundamentals {row['last_fundamentals']} first bar {row['first_bar']}")
    # What the gate has been stopping: the evening strategy runs of the last sessions.
    for row in connection.execute(
            """SELECT task_key, as_of_date, status, count(*) AS n
                 FROM quant.automation_runs
                WHERE as_of_date >= '2026-09-22' AND task_key IN ('post_close_strategy', 'watchlist_main_wave', 'close_review')
                GROUP BY 1, 2, 3 ORDER BY 2, 1, 3""").fetchall():
        print(f"  run {row['as_of_date']} {row['task_key']} {row['status']}: {row['n']}")
