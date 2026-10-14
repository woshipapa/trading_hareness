"""B009: the band main-board ST stocks really have, from how far they moved. A close more than 5.5% from the previous
close cannot happen under a 5% band. Per month since 2026-05: ST main-board bars, those that moved beyond 5.5%, and
the largest move. Also which provider's stored limits say what for 302132. Read-only.
Run inside the owner's quant-research container, stdin = this file."""
from app.database import Database

database = Database()
database.open()
with database.transaction() as connection:
    connection.execute("SET LOCAL statement_timeout = '25s'")
    for row in connection.execute(
            """SELECT to_char(bar.trading_date, 'YYYY-MM') AS month, count(*) AS bars,
                      count(*) FILTER (WHERE abs(bar.close / bar.pre_close - 1) > 0.055) AS beyond,
                      count(*) FILTER (WHERE bar.close / bar.pre_close - 1 > 0.095) AS near_ten_up,
                      round(max(bar.close / bar.pre_close - 1), 4) AS largest_up, round(min(bar.close / bar.pre_close - 1), 4) AS largest_down
                 FROM quant.canonical_bars_daily bar
                 JOIN quant.instruments instrument ON instrument.symbol = bar.symbol AND coalesce(instrument.is_st, false)
                WHERE bar.trading_date >= '2026-05-01' AND bar.quality_status = 'fresh' AND bar.pre_close > 0 AND bar.close > 0
                  AND bar.symbol ~ '^(60[0135]|00[0123])[0-9]{3}\\.(SH|SZ)$'
                GROUP BY 1 ORDER BY 1""").fetchall():
        print(f"  {row['month']}: {row['bars']} ST main-board bars, {row['beyond']} moved more than 5.5%, "
              f"{row['near_ten_up']} closed up more than 9.5%, largest {row['largest_up']} / {row['largest_down']}")
    for row in connection.execute(
            """SELECT bar.selected_provider, round(bar.limit_up / bar.pre_close - 1, 2) AS band, count(*) AS bars,
                      min(bar.trading_date) AS first, max(bar.trading_date) AS last,
                      round(max(abs(bar.close / bar.pre_close - 1)), 4) AS largest_move
                 FROM quant.canonical_bars_daily bar
                WHERE bar.symbol = '302132.SZ' AND bar.trading_date >= '2026-04-01' AND bar.pre_close > 0 AND bar.limit_up IS NOT NULL
                GROUP BY 1, 2 ORDER BY 4""").fetchall():
        print(f"  302132.SZ {row['selected_provider']} band {row['band']}: {row['bars']} bars {row['first']} .. {row['last']}, largest move {row['largest_move']}")
