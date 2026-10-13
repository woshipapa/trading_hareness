"""Hand-run of two peer-side evening steps for today's session: the live limit lists and the limit-up risk reading.

Why by hand: the full post-close refresh that holds these two stages is scheduled only on the owner's own service;
on the peer nothing starts it (found 2026-10-13, BACKLOG B042). Tomorrow's 09:15 auction pool reads this reading.
It calls the same two functions the stages call, with the same arguments, without loading the whole service:
  market_history_backfill.append_limit_performance(day, call, ...)   five licensed read calls, five documents stored
  limit_up_risk_runtime.refresh(database, day, apply=True)            one derived reading stored
The live lists exist only for the current exchange date: run it after 15:00 and before midnight (Asia/Shanghai).

Run inside the owner's quant-research container, stdin = this file:
  ... python -            dry run: fetches the five lists, prints their sizes, writes nothing
  ... python - --apply    stores the lists and the reading (a write to the owner database; idempotent)
"""
import asyncio
import sys
from datetime import datetime, timezone

from app.database import Database
from app.limit_up_risk_runtime import refresh
from app.longhu_vendor_source import intraday_source
from app.market_history_backfill import append_limit_performance, longhu_limit_performance_live
from app.market_session_rules import exchange_date

APPLY = "--apply" in sys.argv
now = datetime.now(timezone.utc)
day = exchange_date(now)
source = intraday_source()


async def call(request):
    return await asyncio.to_thread(source.raw_call, request)


async def run_database(function, *args, timeout_seconds=None):
    return await asyncio.to_thread(function, *args)


print(f"exchange date {day} | apply {APPLY} | source {type(source).__name__}")
if not APPLY:
    lists = asyncio.run(longhu_limit_performance_live(call)(day))
    print("live lists fetched (not stored):", {key: len(rows) for key, (rows, _meta) in lists.items()})
    sys.exit(0)
database = Database()
database.open()
print("lists:", asyncio.run(append_limit_performance(day, call, database=database, run_database=run_database, now=now)))
print("risk reading:", refresh(database, day, apply=True))
