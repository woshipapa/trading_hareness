"""605577.SH, 2023-08-15..2023-11-13: the bars without adj_factor, the price chain through them, and what the factor
table holds for the same dates. Read-only. Run inside the owner's scheduler container, stdin = this file."""
from app.database import Database

SYMBOL = "605577.SH"
database = Database()
database.open()
with database.transaction() as connection:
    connection.execute("SET LOCAL statement_timeout = '25s'")
    bars = connection.execute(
        """SELECT trading_date, close, pre_close, adj_factor, selected_provider,
                  lag(close) OVER (ORDER BY trading_date) AS previous_close
             FROM quant.canonical_bars_daily WHERE symbol=%s AND trading_date BETWEEN '2023-08-15' AND '2023-11-15'
            ORDER BY trading_date""", (SYMBOL,)).fetchall()
    without = [row for row in bars if row["adj_factor"] is None]
    breaks = [row for row in bars if row["previous_close"] is not None and row["pre_close"] is not None
              and abs(float(row["pre_close"]) - float(row["previous_close"])) > 0.005]
    print(f"bars {len(bars)}; without adj_factor {len(without)} ({without[0]['trading_date']}..{without[-1]['trading_date']}); "
          f"bars whose pre_close differs from the close before: {[(str(row['trading_date']), str(row['pre_close']), str(row['previous_close'])) for row in breaks]}")
    print("providers of the bars without a factor:", sorted({row["selected_provider"] for row in without}))
    print("canonical adj_factor values in the range:", sorted({str(row["adj_factor"]) for row in bars if row["adj_factor"] is not None}))
    print("first bars with a factor:", [(str(row["trading_date"]), str(row["adj_factor"])) for row in bars if row["adj_factor"] is not None][:3])
    print("factor table rows in the range, by provider: rows, dates covered among the bars without a factor, distinct values")
    dates = [row["trading_date"] for row in without]
    for row in connection.execute(
            """SELECT provider, count(*) AS rows, count(*) FILTER (WHERE trading_date = ANY(%s)) AS on_gap_dates,
                      array_agg(DISTINCT adj_factor::text) AS factors, min(trading_date) AS first, max(trading_date) AS last
                 FROM quant.daily_adjustment_factors
                WHERE symbol=%s AND trading_date BETWEEN '2023-08-15' AND '2023-11-15' GROUP BY 1 ORDER BY 1""", (dates, SYMBOL)).fetchall():
        print("  ", dict(row))
    columns = [row["column_name"] for row in connection.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='quant' AND table_name='daily_adjustment_factors' ORDER BY ordinal_position").fetchall()]
    print("factor table columns:", columns)
