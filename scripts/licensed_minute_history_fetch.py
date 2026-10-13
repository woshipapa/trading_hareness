#!/usr/bin/env python3
"""Fetch licensed minute history in bounded, resumable batches from the workstation."""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
KEY_FILE = ROOT / "deploy/shared-peer/.env"
DEFAULT_BASE_URL = "http://127.0.0.1:15682"
MAX_WORKERS = 8
DEFAULT_WORKERS = 6
NO_DATA_ERROR = "1020"
CN = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc
Call = Callable[[str, str], Mapping[str, Any]]


def validate_workers(workers: int) -> int:
    if workers > MAX_WORKERS:
        raise ValueError("--workers above 8 is refused: the upstream refuses more than 8 in flight")
    if workers < 1:
        raise ValueError("--workers must be at least 1")
    return workers


def validate_base_url(base_url: str) -> str:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.port == 5681:
        raise ValueError("--base-url :5681 is refused: that is the local service, not owner data")
    return base_url.rstrip("/")


def read_api_key(path: Path = KEY_FILE) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as error:
        raise RuntimeError(f"missing QUANT_SHARED_READ_API_KEY in {path}") from error
    for line in lines:
        name, separator, value = line.partition("=")
        if name == "QUANT_SHARED_READ_API_KEY" and separator and value.strip().strip('"'):
            return value.strip().strip('"')
    raise RuntimeError(f"missing QUANT_SHARED_READ_API_KEY in {path}")


def xshg() -> Any:
    import exchange_calendars

    return exchange_calendars.get_calendar("XSHG")


def session_dates(calendar: Any, start: str, end: str) -> list[str]:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    if end_date < start_date:
        raise ValueError("--end must not be before --start")
    return [session.strftime("%Y%m%d") for session in calendar.sessions_in_range(start, end)]


def last_closed_session(calendar: Any, now: datetime) -> str:
    local_date = now.astimezone(CN).date()
    labels = calendar.sessions_in_range((local_date - timedelta(days=14)).isoformat(), local_date.isoformat())
    now_utc = now.astimezone(UTC)
    for label in reversed(labels):
        close = calendar.session_close(label)
        if hasattr(close, "to_pydatetime"):
            close = close.to_pydatetime()
        if close <= now_utc:
            return label.strftime("%Y%m%d")
    raise ValueError("no closed XSHG session is available")


def session_guard(now: datetime, calendar: Any) -> bool:
    local = now.astimezone(CN)
    if not calendar.is_session(local.date()):
        return False
    return clock_time(9, 0) <= local.time() < clock_time(15, 10)


def wait_for_session(calendar: Any, *, allow_session: bool, now: Callable[[], datetime], sleep: Callable[[float], None],
                     log: Callable[[str], None]) -> None:
    if allow_session:
        return
    while session_guard(now(), calendar):
        local = now().astimezone(CN)
        resume = local.replace(hour=15, minute=10, second=0, microsecond=0)
        log(f"waiting for session guard until {resume:%H:%M}")
        sleep(max(1.0, (resume - local).total_seconds()))


def classify_payload(payload: Mapping[str, Any]) -> str:
    errcode = str(payload.get("errcode") or "0")
    trend = payload.get("trend") or []
    if errcode not in ("0", NO_DATA_ERROR):
        return "refusal"
    return "data" if trend else "no data"


def pending_days(path: Path, days: Iterable[str]) -> list[str]:
    done = set()
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                done.add(json.loads(line)["day"])
    return [day for day in days if day not in done]


def request_payload(code: str, day: str, *, base_url: str, api_key: str) -> Mapping[str, Any]:
    body = json.dumps({"target": "longhu_history", "params": {
        "PhoneOSNew": "1", "VerSion": "5.20.0.2", "c": "StockL2History", "apiv": "w41",
        "a": "GetStockTrend", "StockID": code, "Day": day,
    }}).encode()
    request = urllib.request.Request(
        f"{base_url}/licensed/stock-api/call", data=body,
        headers={"X-Quant-Read-Key": api_key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=40) as response:
        return json.load(response)["pages"][0]["payload"]


def _record(day: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {"day": day, "preclose": payload.get("preclose_px"), "open": payload.get("begin_px"),
            "high": payload.get("hprice"), "low": payload.get("lprice"),
            "total_turnover": payload.get("total_turnover"), "trend": payload.get("trend") or []}


def _until_reached(until: clock_time | None, now: Callable[[], datetime]) -> bool:
    return until is not None and now().astimezone(CN).time() >= until


def fetch(codes: list[str], days: list[str], out: Path, *, workers: int, call: Call, calendar: Any,
          allow_session: bool = False, until: clock_time | None = None,
          now: Callable[[], datetime] = lambda: datetime.now(CN), sleep: Callable[[float], None] = time.sleep) -> str:
    validate_workers(workers)
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "fetch.log"

    def log(message: str) -> None:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now(CN):%H:%M:%S} {message}\n")

    wait_for_session(calendar, allow_session=allow_session, now=now, sleep=sleep, log=log)
    calls = 0
    failures = 0
    durations: list[float] = []
    mark = time.monotonic()
    mark_calls = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for code in codes:
            path = out / f"{code}.jsonl"
            pending = pending_days(path, days)
            futures = {index: pool.submit(_fetch_one, code, day, call, calendar, allow_session, now, sleep, log)
                       for index, day in enumerate(pending[:workers])}
            next_index = len(futures)
            with path.open("a", encoding="utf-8") as handle:
                for index, day in enumerate(pending):
                    if _until_reached(until, now):
                        for item in futures.values():
                            item.cancel()
                        reason = f"PAUSED at {until.strftime('%H:%M')} after {calls} calls; {code} at {day}"
                        log(reason)
                        return reason
                    future = futures.pop(index)
                    payload, elapsed = future.result()
                    calls += 1
                    durations.append(elapsed)
                    if isinstance(payload, Exception):
                        failures += 1
                        log(f"{code} {day} request failed: {type(payload).__name__} {str(payload)[:80]}")
                        if failures >= 20:
                            for item in futures.values():
                                item.cancel()
                            reason = "ABORT: 20 consecutive failed requests"
                            log(reason)
                            return reason
                        if next_index < len(pending):
                            futures[next_index] = pool.submit(_fetch_one, code, pending[next_index], call, calendar,
                                                             allow_session, now, sleep, log)
                            next_index += 1
                        continue
                    status = classify_payload(payload)
                    if status == "refusal":
                        failures += 1
                        log(f"{code} {day} upstream refusal errcode {payload.get('errcode')} {str(payload.get('errmsg'))[:40]}")
                        if failures >= 20:
                            for item in futures.values():
                                item.cancel()
                            reason = "ABORT: 20 consecutive failed requests"
                            log(reason)
                            return reason
                        if next_index < len(pending):
                            futures[next_index] = pool.submit(_fetch_one, code, pending[next_index], call, calendar,
                                                             allow_session, now, sleep, log)
                            next_index += 1
                        continue
                    failures = 0
                    if status in ("data", "no data"):
                        handle.write(json.dumps(_record(day, payload), ensure_ascii=False) + "\n")
                        handle.flush()
                    if calls - mark_calls >= 200:
                        ordered = sorted(durations)
                        p90 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))]
                        log(f"{calls} calls; {code} at {day}; {(calls - mark_calls) / (time.monotonic() - mark):.1f} calls/s "
                            f"with {workers} worker(s); seconds per call median {statistics.median(ordered):.2f} p90 {p90:.2f}")
                        mark, mark_calls, durations = time.monotonic(), calls, []
                    if next_index < len(pending):
                        futures[next_index] = pool.submit(_fetch_one, code, pending[next_index], call, calendar,
                                                         allow_session, now, sleep, log)
                        next_index += 1
    reason = f"FINISHED {calls} calls ({' '.join(codes)})"
    log(reason)
    return reason


def _fetch_one(code: str, day: str, call: Call, calendar: Any, allow_session: bool,
               now: Callable[[], datetime], sleep: Callable[[float], None], log: Callable[[str], None]) -> tuple[Any, float]:
    wait_for_session(calendar, allow_session=allow_session, now=now, sleep=sleep, log=log)
    started = time.monotonic()
    try:
        return call(code, day), time.monotonic() - started
    except Exception as error:
        return error, time.monotonic() - started


def _read_codes(values: list[str], path: Path | None) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines() if path else []
    return list(dict.fromkeys(value.strip() for value in [*values, *lines] if value.strip()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("codes", nargs="*")
    parser.add_argument("--codes-file", type=Path)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--until", type=lambda value: datetime.strptime(value, "%H:%M").time())
    parser.add_argument("--allow-session", action="store_true")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args(argv)
    try:
        workers = validate_workers(args.workers)
        base_url = validate_base_url(args.base_url)
        codes = _read_codes(args.codes, args.codes_file)
        if not codes:
            parser.error("provide codes or --codes-file")
        api_key = read_api_key()
        calendar = xshg()
        end = args.end or last_closed_session(calendar, datetime.now(CN))
        days = session_dates(calendar, args.start, end)
        call = lambda code, day: request_payload(code, day, base_url=base_url, api_key=api_key)
        result = fetch(codes, days, args.out, workers=workers, call=call, calendar=calendar,
                       allow_session=args.allow_session, until=args.until)
    except (RuntimeError, ValueError) as error:
        parser.error(str(error))
    return 0 if result.startswith(("FINISHED", "PAUSED")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
