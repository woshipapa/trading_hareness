"""The teacher's plans in quant.intraday_watchlists.metadata: keys, then plans by playbook / lifecycle / day. Read-only counts."""
import json
from app.database import Database
database = Database(); database.open()
with database.transaction() as c:
    c.execute("SET LOCAL statement_timeout = '30s'")
    total = c.execute("SELECT count(*) AS n, count(*) FILTER (WHERE enabled) AS enabled FROM quant.intraday_watchlists").fetchone()
    print("watchlist rows:", dict(total))
    row = c.execute("SELECT metadata FROM quant.intraday_watchlists WHERE metadata::text LIKE '%playbook%' ORDER BY updated_at DESC LIMIT 1").fetchone()
    meta = row["metadata"] if row and isinstance(row["metadata"], dict) else json.loads(row["metadata"]) if row else {}
    print("metadata keys of the newest plan:", sorted(meta)[:30])
    for key in ("playbook", "source", "lifecycle", "status", "plan_status", "origin", "kind"):
        rows = c.execute(f"SELECT metadata->>'{key}' AS k, count(*) AS n FROM quant.intraday_watchlists WHERE metadata ? '{key}' GROUP BY 1 ORDER BY 2 DESC LIMIT 22").fetchall()
        if rows:
            print("--", key, [(r["k"], r["n"]) for r in rows])
    for r in c.execute("""SELECT (updated_at AT TIME ZONE 'Asia/Shanghai')::date AS day, count(*) AS n
                            FROM quant.intraday_watchlists WHERE metadata::text LIKE '%playbook%'
                           GROUP BY 1 ORDER BY 1 DESC LIMIT 8""").fetchall():
        print("   plans last updated on", r["day"], r["n"])
