"""W007: fetch the minute history of indices and two index funds from the licensed interface, and save today's full-day radar.

Read-only requests through the owner read path (127.0.0.1:15682). Starts at 15:10 Asia/Shanghai (after the close) when
launched earlier. Resumable: a (code, day) already in the output file is skipped. One JSON line per code and day:
{day, preclose, open, high, low, total_turnover, trend: [[HH:MM, price, average price, volume, flag], ...]}.
Output and log: state/research/vshape_20261013/data/ (machine-local, not tracked).
"""
import json, re, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd

CN = timezone(timedelta(hours=8))
ROOT = Path("/Users/papa/codebase/n8n")
OUT = ROOT / "state/research/vshape_20261013/data"
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
UNTIL = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--until=")), None)   # HH:MM Asia/Shanghai: stop cleanly then
PAUSE = float(next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--pause=")), "0.1"))   # seconds a worker rests after a call
WORKERS = int(next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--workers=")), "1"))   # parallel requests
CODES = tuple(ARGS) or ("SH000001", "SZ399001", "SZ399006", "SH000852", "510300", "512100")
KEY = re.search(r"^QUANT_SHARED_READ_API_KEY=(.*)$", (ROOT / "deploy/shared-peer/.env").read_text(), re.M).group(1).strip().strip('"')


def log(text):
    with open(OUT / "fetch.log", "a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now(CN):%H:%M:%S} {text}\n")


def call(code, day):
    body = json.dumps({"target": "longhu_history", "params": {"PhoneOSNew": "1", "VerSion": "5.20.0.2", "c": "StockL2History", "apiv": "w41",
                                                              "a": "GetStockTrend", "StockID": code, "Day": day}}).encode()
    request = urllib.request.Request("http://127.0.0.1:15682/licensed/stock-api/call", data=body,
                                     headers={"X-Quant-Read-Key": KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=40) as response:
        return json.load(response)["pages"][0]["payload"]


OUT.mkdir(parents=True, exist_ok=True)
start = datetime.now(CN).replace(hour=15, minute=10, second=0, microsecond=0)
if datetime.now(CN) < start:
    log(f"waiting for {start:%H:%M}")
    time.sleep((start - datetime.now(CN)).total_seconds())
if not ARGS:
    with urllib.request.urlopen("http://127.0.0.1:18300/api/research/market/radar?trade_date=2026-10-13", timeout=120) as response:
        (OUT.parent / "radar" / "radar_2026-10-13.json").write_bytes(response.read())
    log("saved the full-day radar")
panel = pd.read_parquet(ROOT / "state/research/shortterm_tactics_20261013/data/daily_panel.parquet", columns=["trading_date"])
days = sorted({pd.Timestamp(day).strftime("%Y%m%d") for day in panel["trading_date"].unique() if pd.Timestamp(day) >= pd.Timestamp("2023-09-01")} | {"20261013"})
log(f"{len(days)} sessions, {len(CODES)} codes")
calls = failures = 0
mark, mark_calls, durations = time.time(), 0, []


def fetch_one(code, day):
    """One request and how long it took; a failure comes back as the exception, so results keep the order of the days."""
    started = time.time()
    try:
        payload = call(code, day)
    except Exception as error:  # the network boundary of a long batch: count, say, go on
        return error, time.time() - started
    # 1020 is the interface's "no data for this day"; any other error code is the upstream refusing (a rate limit shows here).
    if not payload.get("trend") and str(payload.get("errcode") or "0") not in ("0", "1020"):
        return RuntimeError(f"upstream errcode {payload.get('errcode')} {str(payload.get('errmsg'))[:40]}"), time.time() - started
    seconds = time.time() - started
    time.sleep(PAUSE)
    return payload, seconds


def stop(futures, text, code):
    for future in futures:
        future.cancel()
    log(text)
    sys.exit(code)


with ThreadPoolExecutor(max_workers=WORKERS) as pool:
    for code in CODES:
        path = OUT / f"{code}.jsonl"
        done = {json.loads(line)["day"] for line in path.open(encoding="utf-8")} if path.exists() else set()
        pending = [day for day in days if day not in done]
        seen_data, empty_run = bool(done), 0
        futures = [pool.submit(fetch_one, code, day) for day in pending]
        with path.open("a", encoding="utf-8") as handle:
            for day, future in zip(pending, futures):
                if UNTIL and datetime.now(CN).strftime("%H:%M") >= UNTIL:
                    stop(futures, f"PAUSED at {UNTIL} after {calls} calls; {code} at {day}. Run the same command again to continue", 0)
                payload, seconds = future.result()
                durations.append(seconds)
                if isinstance(payload, Exception):
                    failures += 1
                    log(f"{code} {day} request failed: {type(payload).__name__} {str(payload)[:80]}")
                    if failures >= 20:
                        stop(futures, "ABORT: 20 consecutive failed requests", 2)
                    continue
                failures = 0
                calls += 1
                trend = payload.get("trend") or []
                if trend:
                    seen_data, empty_run = True, 0
                    handle.write(json.dumps({"day": day, "preclose": payload.get("preclose_px"), "open": payload.get("begin_px"), "high": payload.get("hprice"),
                                             "low": payload.get("lprice"), "total_turnover": payload.get("total_turnover"), "trend": trend}, ensure_ascii=False) + "\n")
                    handle.flush()
                elif seen_data:
                    empty_run += 1
                    if empty_run >= 20:
                        for pending_future in futures:
                            pending_future.cancel()
                        log(f"{code}: 20 consecutive sessions without data after {day}; stopping this code")
                        break
                if calls - mark_calls >= 200:
                    ordered = sorted(durations)
                    log(f"{calls} calls; {code} at {day}; {(calls - mark_calls) / (time.time() - mark):.1f} calls/s with {WORKERS} worker(s); "
                        f"seconds per call median {ordered[len(ordered) // 2]:.2f} p90 {ordered[int(len(ordered) * 0.9)]:.2f}")
                    mark, mark_calls, durations = time.time(), calls, []
        log(f"{code} finished")
log(f"FINISHED {calls} calls ({' '.join(CODES)})")
