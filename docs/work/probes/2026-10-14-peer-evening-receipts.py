"""After release owner-code-20261013-06 (W011): did the peer's evening loop run its own stages?
Read-only; counts, statuses and times only. Run inside the owner's scheduler container, stdin = this file."""
from app.database import Database

database = Database()
database.open()
with database.transaction() as connection:
    connection.execute("SET LOCAL statement_timeout = '25s'")
    print("now (database):", connection.execute("SELECT now() AT TIME ZONE 'Asia/Shanghai' AS t").fetchone()["t"])
    print("receipts of the peer loop's stages, sessions 2026-10-12 and 2026-10-13:")
    for row in connection.execute(
            """SELECT as_of_date, split_part(run_key, ':', 2) AS stage, status,
                      started_at AT TIME ZONE 'Asia/Shanghai' AS started, finished_at AT TIME ZONE 'Asia/Shanghai' AS completed, output_summary,
                      left(coalesce(error_class || ': ' || error_message, ''), 90) AS error
                 FROM quant.automation_runs
                WHERE as_of_date IN ('2026-10-12', '2026-10-13') AND run_key LIKE '%%:peer\\_%%' ESCAPE '\\'
                ORDER BY as_of_date, started_at""").fetchall():
        completed = f"{row['completed']:%m-%d %H:%M:%S}" if row["completed"] else "-"
        print(f"  {row['as_of_date']} {row['stage']:<30} {row['status']:<10} started {row['started']:%m-%d %H:%M:%S} completed {completed} {row['output_summary']} {row['error']}")
    print("stored readings, sessions 2026-10-12 and 2026-10-13:")
    for row in connection.execute(
            """SELECT (effective_at AT TIME ZONE 'Asia/Shanghai')::date AS day, capability, provider_key, count(*) AS documents,
                      max(available_at) AT TIME ZONE 'Asia/Shanghai' AS newest
                 FROM quant.raw_market_observations
                WHERE effective_at >= '2026-10-12' AND effective_at < '2026-10-14'
                  AND capability IN ('limit_performance_history', 'limit_performance_live', 'limit_up_risk_daily',
                                     'market_temperature_daily', 'broad_etf_daily', 'broad_etf_flow_daily', 'market_timing_daily')
                GROUP BY 1, 2, 3 ORDER BY 2, 1, 3""").fetchall():
        print(f"  {row['capability']:<30} {row['day']} {row['provider_key']:<20} documents {row['documents']:>3} newest {row['newest']:%m-%d %H:%M:%S}")
