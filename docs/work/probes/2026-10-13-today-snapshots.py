"""W007, read-only on the owner: the whole-market minute documents of 2026-10-13 at the day's key minutes, as one JSON on stdout.
For each key minute the first document at or after it: symbol, price, cumulative volume and amount; the last document in full."""
import json, sys
from datetime import datetime, timedelta, timezone
from app.database import Database
CN = timezone(timedelta(hours=8))
KEYS = ("09:31", "09:45", "10:00", "10:15", "10:30", "11:00", "11:18", "11:29", "13:01", "13:15", "13:30", "14:00", "14:30", "14:59")
db = Database(); db.open()
out = {"minutes": {}}
with db.transaction() as c:
    c.execute("SET LOCAL statement_timeout = '120s'")
    for key in KEYS:
        hour, minute = map(int, key.split(":"))
        at = datetime(2026, 10, 13, hour, minute, tzinfo=CN)
        row = c.execute("""SELECT effective_at, normalized->'order' AS ord, normalized->'columns' AS cols
                             FROM quant.raw_market_observations
                            WHERE provider_key='fuyao_ths' AND capability='a_share_minute_cross_section' AND symbol IS NULL
                              AND effective_at >= %s AND effective_at < %s ORDER BY effective_at LIMIT 1""", (at, at + timedelta(minutes=3))).fetchone()
        if row is None:
            out["minutes"][key] = None
            continue
        order = row["ord"] if isinstance(row["ord"], list) else json.loads(row["ord"])
        cols = row["cols"] if isinstance(row["cols"], dict) else json.loads(row["cols"])
        out["order"] = order
        keep = order if key == KEYS[-1] else [name for name in order if name in ("symbol", "price", "volume", "amount", "turnover")]
        out["minutes"][key] = {"at": row["effective_at"].astimezone(CN).strftime("%H:%M:%S"), "columns": {name: cols[name]["values"] for name in keep if name in cols and "values" in cols[name]},
                               "other": {name: sorted(cols[name].keys()) for name in order if name in cols and "values" not in cols[name]}}
json.dump(out, sys.stdout, ensure_ascii=False)
