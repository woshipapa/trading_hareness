"""B058/B073: after the minute backfill, which of 2026-10-14's streams still have a hole in 09:21-11:05?
Read-only; counts of covered minutes only. Run inside the owner's quant-research container."""
from app.database import Database

database = Database()
database.open()
WINDOW = ("2026-10-14 09:21:00+08", "2026-10-14 11:06:00+08")
with database.transaction() as c:
    c.execute("SET LOCAL statement_timeout = '40s'")
    rows = c.execute(
        """SELECT provider_key, capability, (symbol IS NULL) AS market_wide, count(*) AS n_day,
                  count(*) FILTER (WHERE effective_at >= %s AND effective_at < %s) AS n_window,
                  count(DISTINCT date_trunc('minute', effective_at)) FILTER (WHERE effective_at >= %s AND effective_at < %s) AS minutes_in_window,
                  count(DISTINCT date_trunc('minute', effective_at)) FILTER (WHERE effective_at >= '2026-10-14 13:00:00+08' AND effective_at < '2026-10-14 15:00:00+08') AS minutes_afternoon
             FROM quant.raw_market_observations
            WHERE effective_at >= '2026-10-14 00:00:00+08' AND effective_at < '2026-10-15 00:00:00+08'
            GROUP BY 1,2,3 HAVING count(*) >= 5 ORDER BY 6 DESC, 4 DESC LIMIT 40""", (*WINDOW, *WINDOW)).fetchall()
    print("stream | market_wide | rows_day | rows_09:21-11:05 | distinct minutes 09:21-11:05 (of 105) | distinct minutes 13:00-14:59 (of 120)")
    for r in rows:
        print(f"{r['provider_key']}/{r['capability']} | {r['market_wide']} | {r['n_day']} | {r['n_window']} | {r['minutes_in_window']} | {r['minutes_afternoon']}")
    for table, ts in (("quant.intraday_minute_sessions", None),):
        try:
            cols = [x["column_name"] for x in c.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='quant' AND table_name='intraday_minute_sessions'").fetchall()]
            print("intraday_minute_sessions columns:", cols[:14])
        except Exception as error:
            print("intraday_minute_sessions: ", type(error).__name__, str(error)[:100])
