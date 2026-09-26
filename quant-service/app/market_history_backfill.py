"""Multi-source market history backfill: limit-up lists, auction lists, breadth.

Tushare is not the only history source the data plane can reach.  Probed on
2026-09-22, these return dated history (2024-03 and later unless noted):

* Longhu history ``DailyLimitPerformance`` (``Day``, ``PidType`` 1-5): each
  day's limit-up / board lists with first-seal time, amounts and themes;
* Fuyao ``a_share_limit_{up,break,down}_pool`` (``date_ms``, 50 a page):
  the same pools with THS reasons, seal money and consecutive-board count;
* Longhu history ``MorningBiddingList`` (``Date``, 60 a page): the opening
  auction list (from 2024-09; 2024-03 came back empty);
* Longhu history ``RiseFallAnalysis`` (250 days a page): market breadth.

Each source walks the SSE calendar on its own task with its own pacing, so
one slow or failing provider never holds the others, and one stored row per
(source, day, list) makes every run resumable.  The Longhu gateway also
serves the live session, so nothing runs 09:00-15:45 on a trading day.
Rows land in ``raw_market_observations`` (research evidence only).

    python -m app.market_history_backfill 2023-09-01 2026-09-19 [--sources a,b]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from psycopg.types.json import Json

_CN = ZoneInfo("Asia/Shanghai")
LONGHU_PROVIDER = "longhuvip_history"
FUYAO_PROVIDER = "fuyao_ths"


@dataclass(frozen=True)
class DayList:
    """One stored list: which provider/capability, which sub-list, which day."""
    provider: str
    capability: str
    list_key: str


def session_close(day: date) -> datetime:
    return datetime.combine(day, time(15, 0), tzinfo=_CN)


def stored(connection: Any, item: DayList, day: date) -> bool:
    row = connection.execute(
        """SELECT 1 FROM quant.raw_market_observations WHERE provider_key=%s AND capability=%s AND market='cn'
            AND symbol=%s AND effective_at=%s LIMIT 1""",
        (item.provider, item.capability, item.list_key, session_close(day))).fetchone()
    return row is not None


def store(connection: Any, item: DayList, day: date, rows: list[Any], meta: dict[str, Any]) -> bool:
    body = {"day": day.isoformat(), "list": item.list_key, "rows": rows, "meta": meta,
            "provider_key": item.provider, "capability": item.capability}
    serialized = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
    body = json.loads(serialized)
    header = {key: body[key] for key in ("day", "list", "provider_key", "capability")}
    header["rows"] = len(rows)
    now = datetime.now(timezone.utc)
    inserted = connection.execute(
        """INSERT INTO quant.raw_market_observations(provider_key,capability,market,symbol,effective_at,available_at,payload_sha256,normalized,payload)
           VALUES(%s,%s,'cn',%s,%s,%s,%s,%s,%s)
           ON CONFLICT(provider_key,capability,market,symbol,effective_at,payload_sha256) DO NOTHING RETURNING observation_id""",
        (item.provider, item.capability, item.list_key, session_close(day), now,
         hashlib.sha256(serialized.encode()).hexdigest(), Json(header), Json(body))).fetchone()
    return inserted is not None


def _payload(response: Any) -> dict[str, Any]:
    pages = (response or {}).get("pages") if isinstance(response, dict) else None
    payload = (pages or [{}])[0].get("payload") if pages else response
    return payload if isinstance(payload, dict) else {}


def _info_rows(payload: dict[str, Any]) -> list[Any]:
    """Longhu lists: ``info`` is either the row list or [row list, extras...]."""
    info = payload.get("info")
    if isinstance(info, list) and info and isinstance(info[0], list) and info[0] and isinstance(info[0][0], list):
        return list(info[0])
    return list(info) if isinstance(info, list) else []


# --- per-source day fetchers (each returns {list_key: (rows, meta)}) ---------

def longhu_limit_performance(call: Callable[[dict[str, Any]], Awaitable[Any]]):
    async def fetch(day: date) -> dict[str, tuple[list[Any], dict[str, Any]]]:
        lists = {}
        for pid in ("1", "2", "3", "4", "5"):
            payload = _payload(await call({"target": "longhu_history", "params": {
                "Order": "0", "st": "1000", "a": "DailyLimitPerformance", "c": "HisHomeDingPan", "PhoneOSNew": "1",
                "VerSion": "5.7.0.12", "Index": "0", "PidType": pid, "apiv": "w31", "Type": "4", "Day": day.isoformat()}}))
            lists[f"pid:{pid}"] = (_info_rows(payload), {"errcode": payload.get("errcode"), "pid_type": pid})
        return lists
    return fetch


def longhu_morning_bidding(call: Callable[[dict[str, Any]], Awaitable[Any]], pages: int = 5):
    async def fetch(day: date) -> dict[str, tuple[list[Any], dict[str, Any]]]:
        rows: list[Any] = []
        for page in range(pages):
            payload = _payload(await call({"target": "longhu_history", "params": {
                "Order": "1", "a": "MorningBiddingList", "st": "60", "c": "HisHomeDingPan", "PhoneOSNew": "1",
                "VerSion": "5.20.0.2", "Index": str(page * 60), "PidType": "0", "Date": day.isoformat(),
                "apiv": "w41", "Type": "4"}}))
            batch = _info_rows(payload)
            rows.extend(batch)
            if len(batch) < 60:
                break
        return {"auction": (rows, {"pages_read": page + 1})}
    return fetch


def fuyao_limit_pools(fetch_fuyao: Callable[[str, dict[str, Any]], Awaitable[Any]], max_pages: int = 20):
    async def fetch(day: date) -> dict[str, tuple[list[Any], dict[str, Any]]]:
        date_ms = int(session_close(day).timestamp() * 1000)
        lists = {}
        for pool in ("limit_up", "limit_break", "limit_down"):
            items: list[Any] = []
            for page in range(1, max_pages + 1):
                data = await fetch_fuyao(f"a_share_{pool}_pool", {"date_ms": date_ms, "page": page, "size": 50})
                batch = (data or {}).get("item") or (data or {}).get("items") or (data or {}).get("data") or []
                items.extend(batch)
                if len(batch) < 50:
                    break
            lists[pool] = (items, {"pages_read": page})
        return lists
    return fetch


SOURCES: dict[str, dict[str, Any]] = {
    "longhu_limit_performance": {"provider": LONGHU_PROVIDER, "capability": "limit_performance_history",
                                 "lists": [f"pid:{pid}" for pid in "12345"], "pace": 0.4},
    "fuyao_limit_pools": {"provider": FUYAO_PROVIDER, "capability": "limit_pool_history",
                          "lists": ["limit_up", "limit_break", "limit_down"], "pace": 0.6},
    "longhu_morning_bidding": {"provider": LONGHU_PROVIDER, "capability": "morning_bidding_history",
                               "lists": ["auction"], "pace": 0.4},
}


def backfill_allowed(now: datetime, trading_day: bool) -> bool:
    """Never while the live session shares the gateways: 09:00-15:45 on a trading day."""
    local = now.astimezone(_CN).time()
    return not (trading_day and time(9, 0) <= local < time(15, 45))


async def walk_source(name: str, days: list[date], fetch: Callable[[date], Awaitable[dict[str, Any]]], *,
                      database: Any, run_database: Callable[..., Awaitable[Any]], allowed: Callable[[], Awaitable[bool]],
                      pace_seconds: float, log: Callable[[str], None] = print) -> dict[str, Any]:
    spec = SOURCES[name]
    items = [DayList(spec["provider"], spec["capability"], key) for key in spec["lists"]]
    report = {"source": name, "days": 0, "stored": 0, "skipped": 0, "empty": 0, "errors": {}, "stopped": None}
    for day in days:
        if not await allowed():
            report["stopped"] = f"session window reached before {day}"
            break

        def done() -> bool:
            with database.transaction() as connection:
                return all(stored(connection, item, day) for item in items)

        report["days"] += 1
        if await run_database(done, timeout_seconds=60):
            report["skipped"] += 1
            continue
        try:
            lists = await fetch(day)
        except Exception as error:  # noqa: BLE001 - one day never stops the walk
            report["errors"][str(day)] = str(error)[:200]
            await asyncio.sleep(pace_seconds)
            continue

        def persist() -> int:
            written = 0
            with database.transaction() as connection:
                for item in items:
                    rows, meta = lists.get(item.list_key, ([], {}))
                    written += int(store(connection, item, day, rows, meta))
            return written

        report["stored"] += await run_database(persist, timeout_seconds=120)
        if not any(lists.get(item.list_key, ([], {}))[0] for item in items):
            report["empty"] += 1
        if report["days"] % 25 == 0:
            log(f"{name} at {day}: {report['days']} days, {report['stored']} lists stored, {report['empty']} empty days, "
                f"{len(report['errors'])} errors")
        await asyncio.sleep(pace_seconds)
    return report


async def market_breadth(call: Callable[[dict[str, Any]], Awaitable[Any]], *, database: Any,
                         run_database: Callable[..., Awaitable[Any]], pages: int = 4) -> dict[str, Any]:
    """Longhu breadth history: 250 days a page, one stored row per day."""
    item = DayList(LONGHU_PROVIDER, "market_breadth_history", "market")
    rows: list[list[Any]] = []
    for page in range(pages):
        payload = _payload(await call({"target": "longhu_history", "params": {
            "a": "RiseFallAnalysis", "st": "250", "apiv": "w43", "c": "HisHomeDingPan", "PhoneOSNew": "1",
            "VerSion": "5.22.0.2", "Index": str(page * 250)}}))
        batch = [row for row in _info_rows(payload) if isinstance(row, list) and row]
        rows.extend(batch)
        if len(batch) < 250:
            break

    def persist() -> int:
        written = 0
        with database.transaction() as connection:
            for row in rows:
                try:
                    day = date.fromisoformat(str(row[-1]))
                except ValueError:
                    continue
                if not stored(connection, item, day):
                    written += int(store(connection, item, day, [row], {"fields": "as served by RiseFallAnalysis"}))
        return written

    return {"source": "longhu_market_breadth", "days_read": len(rows), "stored": await run_database(persist, timeout_seconds=180)}


def main() -> None:  # pragma: no cover - operational entry point
    from . import main as service
    from .fuyao_provider import fetch as fetch_fuyao

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("start", type=date.fromisoformat)
    parser.add_argument("end", type=date.fromisoformat)
    parser.add_argument("--sources", default=",".join([*SOURCES, "longhu_market_breadth"]))
    args = parser.parse_args()
    wanted = set(args.sources.split(","))

    def read_days() -> tuple[list[date], bool]:
        with service.db.transaction() as connection:
            days = [row["calendar_date"] for row in connection.execute(
                """SELECT calendar_date FROM quant.market_trade_calendar WHERE exchange='SSE' AND is_open
                    AND calendar_date BETWEEN %s AND %s ORDER BY calendar_date DESC""", (args.start, args.end)).fetchall()]
            today = datetime.now(_CN).date()
            trading = bool(connection.execute(
                "SELECT 1 FROM quant.market_trade_calendar WHERE exchange='SSE' AND is_open AND calendar_date=%s",
                (today,)).fetchone())
        return days, trading

    async def run() -> list[dict[str, Any]]:
        days, trading_today = await service.run_database_blocking(read_days, timeout_seconds=60)

        async def allowed() -> bool:
            return backfill_allowed(datetime.now(timezone.utc), trading_today)

        fetchers = {
            "longhu_limit_performance": longhu_limit_performance(service.shared_stock_api_call),
            "fuyao_limit_pools": fuyao_limit_pools(fetch_fuyao),
            "longhu_morning_bidding": longhu_morning_bidding(service.shared_stock_api_call),
        }
        tasks = [walk_source(name, days, fetchers[name], database=service.db, run_database=service.run_database_blocking,
                             allowed=allowed, pace_seconds=SOURCES[name]["pace"]) for name in SOURCES if name in wanted]
        if "longhu_market_breadth" in wanted:
            tasks.append(market_breadth(service.shared_stock_api_call, database=service.db,
                                        run_database=service.run_database_blocking))
        return await asyncio.gather(*tasks, return_exceptions=True)

    for report in asyncio.run(run()):
        print(report)


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["DayList", "SOURCES", "backfill_allowed", "fuyao_limit_pools", "longhu_limit_performance",
           "longhu_morning_bidding", "market_breadth", "store", "stored", "walk_source"]
