"""潜龙出海 / leader flow (xiaojie_leader_flow) since 2026-10-08: what the scans said, the candidates and the alerts.
Read-only. Run inside the owner's quant-research container, stdin = this file."""
from app.database import Database

database = Database()
database.open()
with database.transaction() as connection:
    connection.execute("SET LOCAL statement_timeout = '25s'")
    print("now:", connection.execute("SELECT now() AT TIME ZONE 'Asia/Shanghai' AS t").fetchone()["t"])
    for row in connection.execute(
            """SELECT (observed_at AT TIME ZONE 'Asia/Shanghai')::date AS day, count(*) AS scans,
                      coalesce(source_status->'xiaojie_leader_flow'->>'status', 'absent') AS status,
                      min(to_char(observed_at AT TIME ZONE 'Asia/Shanghai', 'HH24:MI')) AS first,
                      max(to_char(observed_at AT TIME ZONE 'Asia/Shanghai', 'HH24:MI')) AS last,
                      left(max(coalesce(source_status->'xiaojie_leader_flow'->>'reason', '')), 110) AS reason
                 FROM quant.intraday_scan_runs
                WHERE observed_at >= '2026-10-08'::timestamp AT TIME ZONE 'Asia/Shanghai'
                GROUP BY 1, 3 ORDER BY 1, 2 DESC""").fetchall():
        print(f"  {row['day']} leader_flow={row['status']:<10} scans={row['scans']:>4} {row['first']}..{row['last']} {row['reason']}")
    for row in connection.execute(
            """SELECT strategy_key, as_of_date, count(*) AS n FROM quant.strategy_daily_candidates
                WHERE as_of_date >= '2026-10-08' GROUP BY 1, 2 ORDER BY 2, 1""").fetchall():
        print(f"  candidates {row['as_of_date']} {row['strategy_key']}: {row['n']}")
    columns = [r["column_name"] for r in connection.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='quant' AND table_name='intraday_alert_deliveries'").fetchall()]
    stamp = next((c for c in ("created_at", "enqueued_at", "next_attempt_at", "delivered_at") if c in columns), None)
    if stamp:
        for row in connection.execute(
                f"""SELECT ({stamp} AT TIME ZONE 'Asia/Shanghai')::date AS day, status, count(*) AS n FROM quant.intraday_alert_deliveries
                     WHERE {stamp} >= '2026-10-08'::timestamp AT TIME ZONE 'Asia/Shanghai' GROUP BY 1, 2 ORDER BY 1, 2""").fetchall():
            print(f"  alerts {row['day']} {row['status']}: {row['n']} (by {stamp})")
