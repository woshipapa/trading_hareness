"""Teacher plans (metadata.teacher_review): keys, then plans by playbook, review day, lifecycle, enabled. Read-only counts."""
import json
from app.database import Database
database = Database(); database.open()
with database.transaction() as c:
    c.execute("SET LOCAL statement_timeout = '30s'")
    row = c.execute("SELECT metadata->'teacher_review' AS t FROM quant.intraday_watchlists WHERE metadata ? 'teacher_review' ORDER BY updated_at DESC LIMIT 1").fetchone()
    t = row["t"] if isinstance(row["t"], dict) else json.loads(row["t"])
    print("teacher_review keys:", sorted(t))
    for key in sorted(t):
        if isinstance(t[key], (str, int, float, bool)) and len(str(t[key])) < 40:
            print("   ", key, "=", t[key])
    for key in ("playbook", "review_date", "trade_date", "lifecycle", "state", "status", "decision", "kind"):
        rows = c.execute(f"""SELECT metadata->'teacher_review'->>'{key}' AS k, enabled, count(*) AS n FROM quant.intraday_watchlists
                              WHERE metadata ? 'teacher_review' GROUP BY 1,2 ORDER BY 3 DESC LIMIT 30""").fetchall()
        if any(r["k"] is not None for r in rows):
            print("--", key, [(r["k"], r["enabled"], r["n"]) for r in rows])
