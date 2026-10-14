"""B009: do main-board ST bars whose STORED band is 5% move beyond 5.5%? Per month and stored band since 2026-09,
and by the name's prefix (ST or *ST) as the instrument row has it today. Read-only.
Run inside the owner's quant-research container, stdin = this file."""
from app.database import Database

database = Database()
database.open()
with database.transaction() as connection:
    connection.execute("SET LOCAL statement_timeout = '25s'")
    for row in connection.execute(
            """SELECT to_char(bar.trading_date, 'YYYY-MM') AS month, round(bar.limit_up / bar.pre_close - 1, 2) AS stored_band,
                      CASE WHEN instrument.name LIKE '*ST%%' THEN '*ST' ELSE 'ST' END AS kind,
                      count(*) AS bars, count(DISTINCT bar.symbol) AS stocks,
                      count(*) FILTER (WHERE abs(bar.close / bar.pre_close - 1) > 0.055) AS beyond,
                      count(*) FILTER (WHERE bar.high > bar.limit_up + 0.005) AS high_above_stored_limit,
                      round(max(abs(bar.close / bar.pre_close - 1)), 4) AS largest
                 FROM quant.canonical_bars_daily bar
                 JOIN quant.instruments instrument ON instrument.symbol = bar.symbol AND coalesce(instrument.is_st, false)
                WHERE bar.trading_date >= '2026-09-01' AND bar.quality_status = 'fresh' AND bar.pre_close > 0 AND bar.close > 0
                  AND bar.limit_up IS NOT NULL AND bar.symbol ~ '^(60[0135]|00[0123])[0-9]{3}\\.(SH|SZ)$'
                GROUP BY 1, 2, 3 ORDER BY 1, 2, 3""").fetchall():
        print(f"  {row['month']} stored band {row['stored_band']} {row['kind']:<3}: {row['bars']} bars, {row['stocks']} stocks, "
              f"{row['beyond']} moved beyond 5.5%, {row['high_above_stored_limit']} traded above the stored up-limit, largest move {row['largest']}")
    for row in connection.execute(
            """SELECT bar.trading_date, round(bar.limit_up / bar.pre_close - 1, 2) AS stored_band, count(*) AS bars,
                      count(*) FILTER (WHERE bar.high > bar.limit_up + 0.005) AS above
                 FROM quant.canonical_bars_daily bar
                 JOIN quant.instruments instrument ON instrument.symbol = bar.symbol AND coalesce(instrument.is_st, false)
                WHERE bar.trading_date >= '2026-09-15' AND bar.quality_status = 'fresh' AND bar.pre_close > 0
                  AND bar.limit_up IS NOT NULL AND bar.symbol ~ '^(60[0135]|00[0123])[0-9]{3}\\.(SH|SZ)$'
                GROUP BY 1, 2 ORDER BY 1, 2""").fetchall():
        print(f"  {row['trading_date']} stored band {row['stored_band']}: {row['bars']} bars, {row['above']} traded above the stored up-limit")
