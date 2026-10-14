"""Can the 09:25 auction result of 2026-10-14 still be read after the host's outage? Read-only: one provider call
for three pool stocks, and the shape of what the capture stored after the reboot. Nothing is written.
Run inside the owner's quant-research container, stdin = this file."""
import asyncio, json
from datetime import date

from app.main import db, fetch_fuyao_data, read_auction_pool, read_auction_scored_rows

DAY = date(2026, 10, 14)
pool = read_auction_pool(db, DAY)
scored = read_auction_scored_rows(db, DAY)
print("pool", len(pool), "scored rows", len(scored), "first", pool[:3])
with db.transaction() as connection:
    connection.execute("SET LOCAL statement_timeout = '25s'")
    for row in connection.execute(
            """SELECT capability, count(*) AS n, min(available_at AT TIME ZONE 'Asia/Shanghai') AS first_stored,
                      max(available_at AT TIME ZONE 'Asia/Shanghai') AS last_stored
                 FROM quant.raw_market_observations
                WHERE capability IN ('auction_pool_snapshot', 'auction_curve_daily', 'auction_bid_daily')
                  AND effective_at >= '2026-10-14'::timestamp AT TIME ZONE 'Asia/Shanghai'
                  AND effective_at < '2026-10-15'::timestamp AT TIME ZONE 'Asia/Shanghai'
                GROUP BY 1""").fetchall():
        print(f"  {row['capability']}: {row['n']} documents, stored {row['first_stored']:%H:%M:%S} .. {row['last_stored']:%H:%M:%S}")
    last = connection.execute(
        """SELECT normalized FROM quant.raw_market_observations
            WHERE capability = 'auction_pool_snapshot' AND effective_at >= '2026-10-14'::timestamp AT TIME ZONE 'Asia/Shanghai'
            ORDER BY effective_at DESC LIMIT 1""").fetchone()
    document = last["normalized"] if isinstance(last["normalized"], dict) else json.loads(last["normalized"])
    print("  last pool snapshot:", document.get("scheduled_at"), document.get("status"), "rows", len(document.get("rows") or []))
    if document.get("rows"):
        print("  its first row:", json.dumps(document["rows"][0], ensure_ascii=False)[:600])
    curve = connection.execute(
        """SELECT symbol, normalized FROM quant.raw_market_observations
            WHERE capability = 'auction_curve_daily' AND effective_at >= '2026-10-14'::timestamp AT TIME ZONE 'Asia/Shanghai'
            ORDER BY symbol LIMIT 1""").fetchone()
    if curve:
        body = curve["normalized"] if isinstance(curve["normalized"], dict) else json.loads(curve["normalized"])
        points = body.get("curve") or []
        print("  curve of", curve["symbol"] or body.get("observation_symbol"), "points", len(points))
        print("  first two:", json.dumps(points[:2], ensure_ascii=False)[:400])
        print("  last two:", json.dumps(points[-2:], ensure_ascii=False)[:400])


async def main() -> None:
    payload = await fetch_fuyao_data("a_share_auction_snapshot", {"thscodes": ",".join(pool[:3])})
    print("total", payload.get("total"), "timestamp", payload.get("timestamp")); print("now from the provider: auction_phase", payload.get("auction_phase"), "data_status", payload.get("data_status"),
          "keys", sorted(payload)[:12])
    items = payload.get("item") or []
    print("  items", len(items) if isinstance(items, list) else type(items).__name__)
    if isinstance(items, list) and items:
        print("  first item:", json.dumps(items[0], ensure_ascii=False)[:700])


asyncio.run(main())
