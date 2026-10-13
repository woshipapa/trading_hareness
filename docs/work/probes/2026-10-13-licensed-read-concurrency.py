"""How many licensed read calls per second does the path behind the peer service take, without the service's
8-thread executor in the way? Read-only: calls the same source object the service uses (peer -> owner gateway ->
vendor) from N threads, for sessions whose data is already fetched; nothing is stored.
Run inside the owner's quant-research container, stdin = this file; argv: thread counts, e.g. "- 12 16".
Stops raising the level at the first upstream error code or failed call."""
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

from app.database import Database
from app.longhu_vendor_source import intraday_source

LEVELS = [int(value) for value in sys.argv[1:]] or [12, 16]
CALLS = 240
database = Database()
database.open()
with database.transaction() as connection:
    days = [row["calendar_date"].strftime("%Y%m%d") for row in connection.execute(
        """SELECT calendar_date FROM quant.market_trade_calendar WHERE exchange='SSE' AND is_open
            AND calendar_date < '2026-10-13' ORDER BY calendar_date DESC LIMIT %s""", (CALLS,)).fetchall()]
source = intraday_source()


def one(day):
    started = time.time()
    try:
        response = source.raw_call({"target": "longhu_history", "params": {
            "PhoneOSNew": "1", "VerSion": "5.20.0.2", "c": "StockL2History", "apiv": "w41", "a": "GetStockTrend",
            "StockID": "510300", "Day": day}})
        payload = response["pages"][0]["payload"]
        ok = bool(payload.get("trend")), str(payload.get("errcode") or "0")
    except Exception as error:
        ok = False, type(error).__name__
    return time.time() - started, ok


print("asked at", datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S"), "| source", type(source).__name__, "| calls per level", len(days))
for level in LEVELS:
    started = time.time()
    with ThreadPoolExecutor(max_workers=level) as pool:
        results = list(pool.map(one, days))
    elapsed = time.time() - started
    seconds = sorted(duration for duration, _ in results)
    bad = [status for _, (good, status) in results if not good]
    print(f"threads {level:>2}: {len(days) / elapsed:5.1f} calls/s | seconds per call median {seconds[len(seconds) // 2]:.2f} "
          f"p90 {seconds[int(len(seconds) * 0.9)]:.2f} max {seconds[-1]:.2f} | without data or failed: {len(bad)} {sorted(set(bad))[:4]}")
    if bad:
        print("stopping: the upstream refused or failed at this level")
        break
    time.sleep(5)
