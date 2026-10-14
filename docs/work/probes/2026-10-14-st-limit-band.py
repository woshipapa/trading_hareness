"""B009: which band do main-board ST stocks and the 302xxx codes really have? From the stored limit prices, read-only.
The repository's rule gives main-board ST 10% from 2026-07-06; the limits stored for 2026-10-12 looked like 5%
(decision 0018). Counts by month, by band and by who wrote the bar. Run inside the owner's quant-research
container, stdin = this file."""
from app.database import Database

database = Database()
database.open()
with database.transaction() as connection:
    connection.execute("SET LOCAL statement_timeout = '25s'")
    print("now (database):", connection.execute("SELECT now() AT TIME ZONE 'Asia/Shanghai' AS t").fetchone()["t"])
    # Main-board stocks whose instrument row says ST today, month by month since 2026-05.
    for row in connection.execute(
            """SELECT to_char(bar.trading_date, 'YYYY-MM') AS month, round(bar.limit_up / bar.pre_close - 1, 2) AS band,
                      bar.selected_provider, count(*) AS bars, count(DISTINCT bar.symbol) AS stocks
                 FROM quant.canonical_bars_daily bar
                 JOIN quant.instruments instrument ON instrument.symbol = bar.symbol AND coalesce(instrument.is_st, false)
                WHERE bar.trading_date >= '2026-05-01' AND bar.quality_status = 'fresh'
                  AND bar.limit_up IS NOT NULL AND bar.pre_close > 0
                  AND bar.symbol ~ '^(60[0135]|00[0123])[0-9]{3}\\.(SH|SZ)$'
                GROUP BY 1, 2, 3 ORDER BY 1, 5 DESC LIMIT 60""").fetchall():
        print(f"  ST main board {row['month']} band {row['band']} bar from {row['selected_provider']}: "
              f"{row['bars']} bars, {row['stocks']} stocks")
    # The same from the limits table, which says how each row was derived.
    for row in connection.execute(
            """SELECT to_char(limits.trading_date, 'YYYY-MM') AS month, limits.provider,
                      round(limits.limit_up / bar.pre_close - 1, 2) AS band, count(*) AS rows
                 FROM quant.daily_trade_limits limits
                 JOIN quant.canonical_bars_daily bar ON bar.symbol = limits.symbol AND bar.trading_date = limits.trading_date
                 JOIN quant.instruments instrument ON instrument.symbol = limits.symbol AND coalesce(instrument.is_st, false)
                WHERE limits.trading_date >= '2026-09-01' AND bar.pre_close > 0
                  AND limits.symbol ~ '^(60[0135]|00[0123])[0-9]{3}\\.(SH|SZ)$'
                GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC LIMIT 30""").fetchall():
        print(f"  limits table {row['month']} {row['provider']} band {row['band']}: {row['rows']} rows")
    # The 302xxx codes.
    for row in connection.execute(
            """SELECT bar.symbol, round(bar.limit_up / bar.pre_close - 1, 2) AS band, count(*) AS bars,
                      min(bar.trading_date) AS first, max(bar.trading_date) AS last
                 FROM quant.canonical_bars_daily bar
                WHERE bar.symbol ~ '^302[0-9]{3}\\.SZ$' AND bar.limit_up IS NOT NULL AND bar.pre_close > 0
                  AND bar.trading_date >= '2026-01-01'
                GROUP BY 1, 2 ORDER BY 1, 3 DESC LIMIT 20""").fetchall():
        print(f"  {row['symbol']} band {row['band']}: {row['bars']} bars, {row['first']} .. {row['last']}")
