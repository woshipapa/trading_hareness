"""Does the history interface already serve the limit lists of the session that just closed? Read-only: five licensed
calls for each of two days, nothing stored. Run inside the owner's quant-research container, stdin = this file."""
import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.longhu_vendor_source import intraday_source
from app.market_history_backfill import longhu_limit_performance

source = intraday_source()


async def call(request):
    return await asyncio.to_thread(source.raw_call, request)


async def main():
    print("asked at", datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"), "Asia/Shanghai")
    for day in (date(2026, 10, 12), date(2026, 10, 13)):
        lists = await longhu_limit_performance(call)(day)
        print(day, "history lists:", {key: (len(rows), meta.get("errcode")) for key, (rows, meta) in lists.items()})

asyncio.run(main())
