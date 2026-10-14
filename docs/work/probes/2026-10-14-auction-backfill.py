"""Store the 09:25 auction result of 2026-10-14 that the capture missed (the host was down from about 09:21).

The provider still answers with the day's final auction (auction_phase closed, data_status final): opening price,
matched volume and amount, unmatched volume. One late tick is stored as the pool snapshot of 09:25:05 and the
auction desk table is built from it, both marked as fetched late (their available_at is the real fetch time).
The auction curves the capture stored after the reboot are used as a cross-check of the opening price.

Dry run unless the environment has AUCTION_BACKFILL_APPLY=1. Refuses when a table of the day exists.
Run inside the owner's quant-research container, stdin = this file."""
import asyncio, collections, json, os
from datetime import date, datetime, time, timezone

from app import derived_daily_readings
from app.auction_open_desk import build_auction_table
from app.auction_pool_runtime import AUCTION_CAPABILITY, AUCTION_PROVIDER, CN_TZ, TABLE_CAPABILITY
from app.main import db, fetch_fuyao_data, read_auction_pool, read_auction_scored_rows
from app.market_event_capture import normalize_fuyao_auction
from app.public_market_repository import persist_timed_observations

DAY = date(2026, 10, 14)
APPLY = os.environ.get("AUCTION_BACKFILL_APPLY") == "1"
REASON = "the owner host was unresponsive from about 09:21 to 11:04 on 2026-10-14 (BACKLOG B058)"


async def main() -> None:
    assert datetime.now(CN_TZ).date() == DAY, "the provider holds the final auction of the current session only"
    pool = read_auction_pool(db, DAY)
    scored = [row for row in read_auction_scored_rows(db, DAY) if str(row["symbol"]) in pool]
    with db.transaction() as connection:
        connection.execute("SET LOCAL statement_timeout = '25s'")
        existing = derived_daily_readings.newest(connection, TABLE_CAPABILITY, DAY, DAY)
        curves = {}
        for row in connection.execute(
                """SELECT symbol, normalized FROM quant.raw_market_observations
                    WHERE provider_key = 'tdx_public' AND capability = 'auction_curve_daily'
                      AND effective_at >= %s::timestamp AT TIME ZONE 'Asia/Shanghai'
                      AND effective_at < (%s::date + 1)::timestamp AT TIME ZONE 'Asia/Shanghai'""", (DAY, DAY)).fetchall():
            body = row["normalized"] if isinstance(row["normalized"], dict) else json.loads(row["normalized"])
            if body.get("curve"):
                curves[row["symbol"] or body["curve"][-1]["symbol"]] = body["curve"][-1]
    print("pool", len(pool), "scored in pool", len(scored), "stored curves", len(curves), "existing tables", len(existing))
    if existing:
        print("refusing: the day already has an auction table")
        return
    scheduled_at = datetime.combine(DAY, time(9, 25, 5), tzinfo=CN_TZ)
    rows, states = [], collections.Counter()
    for offset in range(0, len(pool), 100):
        payload = await fetch_fuyao_data("a_share_auction_snapshot", {"thscodes": ",".join(pool[offset:offset + 100])})
        states[(payload.get("auction_phase"), payload.get("data_status"))] += 1
        rows.extend(event["raw"] for event in normalize_fuyao_auction(payload, scheduled_at))
    print("provider state per call:", dict(states), "rows", len(rows))
    if set(states) != {("closed", "final")} or len(rows) < len(pool) * 0.95:
        print("refusing: the provider does not hold the final auction for the pool")
        return
    # The opening price against the last point of the stored auction curve (09:24:5x), where one exists.
    compared = agree = 0
    differ = []
    for row in rows:
        point = curves.get(row.get("thscode"))
        price = row.get("open_price") or row.get("auction_price")
        if point and price:
            compared += 1
            if abs(float(point["price"]) - float(price)) < 0.011:
                agree += 1
            else:
                differ.append((row["thscode"], price, round(float(point["price"]), 2), point["time"]))
    print(f"opening price against the curve's last point: {agree}/{compared} within a cent; differing (stock, open, curve, time): {differ[:8]}")
    fetched_at = datetime.now(timezone.utc)
    late = {"fetched_at": fetched_at.isoformat(), "reason": REASON,
            "note": "auction_* and open_price are the session's final auction; last_price and float_market_cap are of the fetch time"}
    table = {**build_auction_table(scored, rows, trade_date=DAY), "backfilled": late}
    print("table coverage:", table["coverage"], "buckets:", dict(collections.Counter(row["bucket"] for row in table["rows"])))
    print("sample rows:", json.dumps(table["rows"][:2], ensure_ascii=False)[:500])
    if not APPLY:
        print("dry run: nothing stored")
        return
    snapshot = {"scheduled_at": scheduled_at.isoformat(), "pool_size": len(pool), "rows": rows, "status": "final",
                "research_only": True, "live_effect": "none", "backfilled": late,
                "effective_at": scheduled_at.isoformat(), "available_at": fetched_at.isoformat()}
    print("stored pool snapshot:", persist_timed_observations(db, AUCTION_PROVIDER, AUCTION_CAPABILITY, [snapshot]))
    print("stored table:", derived_daily_readings.store(db, TABLE_CAPABILITY, [table]))


asyncio.run(main())
