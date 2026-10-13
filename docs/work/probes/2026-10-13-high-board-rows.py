"""Why does the risk reading of 2026-10-13 hold no stock above three boards? Shapes of the stored limit-list rows.

Read-only, small. Run inside the owner's quant-research container, stdin = this file."""
import json
from collections import Counter

from app.database import Database

database = Database()
database.open()
with database.transaction() as connection:
    connection.execute("SET LOCAL statement_timeout = '20s'")
    rows = connection.execute(
        """SELECT (effective_at AT TIME ZONE 'Asia/Shanghai')::date AS day, provider_key, symbol AS list_key, payload
             FROM quant.raw_market_observations
            WHERE capability IN ('limit_performance_live', 'limit_performance_history')
              AND effective_at >= '2026-09-29' AND effective_at < '2026-10-14'
            ORDER BY effective_at, provider_key, symbol, available_at DESC""").fetchall()
seen = set()
for row in rows:
    key = (row["day"], row["provider_key"], row["list_key"])
    if key in seen:
        continue
    seen.add(key)
    body = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
    items = body.get("rows") or body.get("list") or []
    lengths = Counter(len(item) if isinstance(item, list) else type(item).__name__ for item in items)
    if row["list_key"] in ("pid:3", "pid:4", "pid:5") or row["day"].isoformat() >= "2026-10-12":
        first = items[0] if items else None
        shown = [str(value)[:12] for value in first[:6]] if isinstance(first, list) else str(first)[:60]
        print(f"{row['day']} {row['provider_key']:<18} {row['list_key']} rows {len(items):>3} lengths {dict(lengths)} first {shown}")
with database.transaction() as connection:
    for row in connection.execute(
            """SELECT (effective_at AT TIME ZONE 'Asia/Shanghai')::date AS day, payload->'coverage' AS coverage,
                      jsonb_array_length(payload->'rows') AS n,
                      (SELECT max((item->>'boards')::float) FROM jsonb_array_elements(payload->'rows') item) AS top_board
                 FROM quant.raw_market_observations
                WHERE capability = 'limit_up_risk_daily' AND effective_at >= '2026-09-22' AND effective_at < '2026-10-14'
                ORDER BY effective_at, available_at""").fetchall():
        print(f"risk reading {row['day']} rows {row['n']} top board {row['top_board']} coverage {row['coverage']}")
