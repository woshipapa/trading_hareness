"""Is the provider's late "final auction" opening price the day's real open? Compare it, for the pool, with the open in
the newest TDX all-A snapshot (another vendor). Read-only; one provider call.
Run inside the owner's quant-research container, stdin = this file."""
import asyncio, json
from datetime import date

from app.main import db, fetch_fuyao_data, read_auction_pool

DAY = date(2026, 10, 14)
pool = read_auction_pool(db, DAY)
with db.transaction() as connection:
    connection.execute("SET LOCAL statement_timeout = '25s'")
    row = connection.execute(
        """SELECT effective_at AT TIME ZONE 'Asia/Shanghai' AS at, normalized FROM quant.raw_market_observations
            WHERE provider_key = 'tdx_public' AND capability = 'a_share_minute_cross_section_shadow' AND symbol IS NULL
            ORDER BY effective_at DESC LIMIT 1""").fetchone()
body = row["normalized"] if isinstance(row["normalized"], dict) else json.loads(row["normalized"])
print("document: order", len(body["order"]), "first", body["order"][:2], "columns", sorted(body["columns"])[:30])
print("tdx snapshot at", row["at"])
wanted = set(pool)
symbols, opens = body["columns"]["symbol"], body["columns"]["raw.open_price"]
print("symbol column", type(symbols).__name__, str(symbols)[:160], "| open column", str(opens)[:120])
codes = {symbol[:6]: symbol for symbol in wanted}
if isinstance(symbols, dict):
    symbols, opens = symbols.get("values") or symbols.get("data") or [], (opens.get("values") or opens.get("data") or []) if isinstance(opens, dict) else opens
tdx = {codes[str(symbol)[:6]]: opens[index] for index, symbol in enumerate(symbols) if str(symbol)[:6] in codes and opens[index] is not None}
print("pool stocks found in the TDX snapshot", len(tdx))


async def main() -> None:
    payload = await fetch_fuyao_data("a_share_auction_snapshot", {"thscodes": ",".join(pool[:100])})
    same, differ, missing = 0, [], 0
    for item in payload.get("item") or []:
        other = tdx.get(item["thscode"])
        if other is None:
            missing += 1
        elif abs(float(other) - float(item["open_price"])) < 0.0051:
            same += 1
        else:
            differ.append((item["thscode"], item["open_price"], other))
    print(f"open price equal to the TDX snapshot's open: {same}; different: {differ[:10]}; not in the TDX snapshot: {missing}")
    print("auction_price equals open_price for", sum(item.get("auction_price") == item.get("open_price") for item in payload["item"]), "of", len(payload["item"]))


asyncio.run(main())
