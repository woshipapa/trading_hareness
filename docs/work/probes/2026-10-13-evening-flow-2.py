"""Follow-up to 2026-10-13-evening-flow.py: is the limit list of the day stored, what does the completion receipt say,
and what has the evening flow fetched since 18:30. Read-only; counts, times and short lists only.
Run inside the owner's quant-research container, stdin = this file."""
import json
from datetime import date

from app.database import Database

database = Database()
database.open()
with database.transaction() as connection:
    connection.execute("SET LOCAL statement_timeout = '25s'")
    print("now (database):", connection.execute("SELECT now() AT TIME ZONE 'Asia/Shanghai' AS t").fetchone()["t"])
    for row in connection.execute(
            """SELECT (effective_at AT TIME ZONE 'Asia/Shanghai')::date AS day, provider_key, capability, count(*) AS documents,
                      max(available_at) AT TIME ZONE 'Asia/Shanghai' AS newest
                 FROM quant.raw_market_observations
                WHERE capability IN ('limit_performance_history', 'limit_performance_live', 'limit_up_risk_daily',
                                     'market_temperature_daily', 'broad_etf_flow_daily', 'auction_open_desk_daily')
                  AND effective_at >= '2026-10-09' AND effective_at < '2026-10-14'
                GROUP BY 1, 2, 3 ORDER BY 3, 1, 2""").fetchall():
        print(f"  {row['capability']:<28} {row['day']} {row['provider_key']:<18} documents {row['documents']:>3} newest {row['newest']:%m-%d %H:%M}")
    receipts = connection.execute(
        """SELECT status, started_at AT TIME ZONE 'Asia/Shanghai' AS started, metadata
             FROM quant.fetch_runs WHERE trade_date = '2026-10-13' AND capability = 'daily_close_completion'
            ORDER BY started_at""").fetchall()
    print(f"completion receipts today: {len(receipts)}; first {receipts[0]['started']:%H:%M:%S}, last {receipts[-1]['started']:%H:%M:%S}")
    for label, row in (("first", receipts[0]), ("last", receipts[-1])):
        metadata = row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"])
        short = {key: (len(value) if isinstance(value, (list, dict)) else value) for key, value in metadata.items() if key != "request_key"}
        print(f"  {label} ({row['status']}): {json.dumps(short, ensure_ascii=False, default=str)[:420]}")
        if label == "last":
            print("  missing symbols in the last receipt:", ", ".join(metadata.get("missing", [])[:40]))
            for key in ("reasons", "remaining", "unresolved", "skipped"):
                if key in metadata:
                    print(f"  {key}:", json.dumps(metadata[key], ensure_ascii=False)[:500])
    print("fetch runs of the day started after 18:30, by capability and status:")
    for row in connection.execute(
            """SELECT capability, status, count(*) AS runs, min(started_at) AT TIME ZONE 'Asia/Shanghai' AS first,
                      max(started_at) AT TIME ZONE 'Asia/Shanghai' AS last
                 FROM quant.fetch_runs
                WHERE started_at >= '2026-10-13 18:30+08' GROUP BY 1, 2 ORDER BY max(started_at) DESC LIMIT 25""").fetchall():
        print(f"  {row['capability']:<36} {row['status']:<10} runs {row['runs']:>3}  {row['first']:%H:%M} .. {row['last']:%H:%M}")
    suspended = connection.execute(
        """SELECT count(*) AS n FROM quant.canonical_bars_daily WHERE trading_date = '2026-10-13' AND coalesce(is_suspended, false)""").fetchone()
    print("bars of the day flagged suspended:", suspended["n"])
