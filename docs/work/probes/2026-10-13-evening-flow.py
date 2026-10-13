"""Tonight's evening flow on the owner: stage receipts, the completion receipt, the lists and readings for the day.

Read-only, small: counts and times only, no document bodies (the new owner host has under 1 GB of memory).
Run inside the owner's quant-research container (calling line in docs/work/README.md), stdin = this file.
"""
from datetime import date

from app.database import Database

DAY = date(2026, 10, 13)
database = Database()
database.open()
with database.transaction() as connection:
    connection.execute("SET LOCAL statement_timeout = '20s'")
    print("now (database):", connection.execute("SELECT now() AT TIME ZONE 'Asia/Shanghai' AS t").fetchone()["t"])
    rows = connection.execute(
        """SELECT input_summary->>'stage' AS stage, status,
                  started_at AT TIME ZONE 'Asia/Shanghai' AS started, updated_at AT TIME ZONE 'Asia/Shanghai' AS updated,
                  left(coalesce(output_summary->>'reason', error_message, ''), 90) AS detail
             FROM quant.automation_runs
            WHERE task_key = 'post_close_refresh.stage' AND as_of_date = %s
            ORDER BY updated_at""", (DAY,)).fetchall()
    print(f"stage receipts for {DAY}: {len(rows)}; updated after 18:30:")
    for row in rows:
        if row["updated"].hour * 60 + row["updated"].minute >= 18 * 60 + 30:
            print(f"  {row['updated']:%H:%M:%S} {row['status']:<10} {row['stage']:<34} started {row['started']:%H:%M:%S} {row['detail']}")
    not_done = [f"{row['stage']}={row['status']}" for row in rows if row["status"] != "completed"]
    print("stages not completed:", not_done)
    for row in connection.execute(
            """SELECT capability, status, started_at AT TIME ZONE 'Asia/Shanghai' AS started,
                      left(metadata::text, 260) AS metadata
                 FROM quant.fetch_runs
                WHERE trade_date = %s AND capability IN ('daily_close_completion')
                ORDER BY started_at""", (DAY,)).fetchall():
        print(f"fetch_runs {row['capability']} {row['status']} {row['started']:%H:%M:%S} {row['metadata']}")
    bars = connection.execute(
        """SELECT count(*) AS bars, count(*) FILTER (WHERE adj_factor > 0) AS with_factor,
                  count(*) FILTER (WHERE limit_up > 0) AS with_limits
             FROM quant.canonical_bars_daily WHERE trading_date = %s""", (DAY,)).fetchone()
    print("daily bars of the day:", dict(bars))
    for row in connection.execute(
            """SELECT provider_key, capability, count(*) AS documents,
                      max(available_at) AT TIME ZONE 'Asia/Shanghai' AS newest
                 FROM quant.raw_market_observations
                WHERE capability IN ('limit_performance_history', 'limit_performance_live')
                  AND effective_at >= %s::date AND effective_at < %s::date + 1
                GROUP BY 1, 2 ORDER BY 1, 2""", (DAY, DAY)).fetchall():
        print("limit lists:", dict(row))
    for row in connection.execute(
            """SELECT capability, count(*) AS documents, max(available_at) AT TIME ZONE 'Asia/Shanghai' AS newest
                 FROM quant.raw_market_observations
                WHERE capability IN ('limit_up_risk_daily', 'market_temperature_daily', 'broad_etf_flow_daily')
                  AND effective_at >= %s::date AND effective_at < %s::date + 1
                GROUP BY 1 ORDER BY 1""", (DAY, DAY)).fetchall():
        print("readings of the day:", dict(row))
