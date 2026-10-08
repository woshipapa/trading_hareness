"""Intraday market context for today's teacher plans, refreshed alongside each watch scan.

Four independent refreshes feed the teacher-review rules through in-memory
books: the 09:25 opening-auction facts, per-sector limit-up counts, the minute
snapshot tape rebuilt after a restart, and 30/60-minute divergence. Each one
throttles itself, never raises, and leaves the previous value in force when it
cannot refresh - the scan never waits on this. Moved out of main.py
(docs/decisions/0008); main builds one TeacherIntradayContext per process.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, time, timezone
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

from .teacher_review_rules import (
    active_plan,
    count_sector_limit_ups,
    divergence_plan_symbols,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
DIVERGENCE_MAX_SYMBOLS = 10


def optional_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class TeacherContextDependencies:
    database: Any
    run_database: Callable[..., Awaitable[Any]]
    run_vendor_blocking: Callable[..., Awaitable[Any]]
    fetch_fuyao: Callable[[str, dict[str, Any]], Awaitable[Any]]
    longhu_configured: Callable[[], bool]
    longhu_source: Callable[[], Any]
    tencent_period_bars: Callable[..., Awaitable[list[dict[str, Any]]]]
    safe_error: Callable[[str, int], str]
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


class TeacherIntradayContext:
    def __init__(self, deps: TeacherContextDependencies, *, market_book: Any, divergence_book: Any, tape: Any) -> None:
        self.deps = deps
        self.market_book = market_book
        self.divergence_book = divergence_book
        self.tape = tape
        self._auction_attempt: dict[str, datetime] = {}
        self._tape_rehydration: dict[str, Any] = {}

    def plans_today(self, watches: list[dict[str, Any]], observed_at: datetime) -> list[tuple[str, dict[str, Any]]]:
        return [(str(watch["symbol"]).upper(), plan) for watch in watches
                if (plan := active_plan(watch, observed_at)) is not None]

    async def refresh(self, watches: list[dict[str, Any]]) -> dict[str, Any]:
        divergence, auction, sectors, tape = await asyncio.gather(
            self.refresh_divergence(watches), self.refresh_auction(watches), self.refresh_sectors(watches),
            self.rehydrate_tape())
        return {"divergence": divergence, "auction": auction, "sectors": sectors, "tape": tape}

    async def refresh_auction(self, watches: list[dict[str, Any]]) -> dict[str, Any]:
        """09:25 opening-auction facts for today's teacher plans (data plane: Fuyao auction snapshot).

        One request (<=100 codes) per attempt, at most every 30 s, until the
        snapshot is final; then the book serves it all day.  Never raises.
        """
        from .market_event_capture import normalize_fuyao_auction
        try:
            observed_at = self.deps.now()
            local = observed_at.astimezone(SHANGHAI)
            if not (time(9, 25, 5) <= local.time() <= time(15, 0)):
                return {"status": "outside_window"}
            plans = dict(self.plans_today(watches, observed_at))
            missing = self.market_book.auction_missing(list(plans), observed_at)[:100]
            if not missing:
                return {"status": "fresh"}
            last = self._auction_attempt.get("at")
            if last is not None and (observed_at - last).total_seconds() < 30:
                return {"status": "throttled", "missing": len(missing)}
            self._auction_attempt["at"] = observed_at
            data = await asyncio.wait_for(
                self.deps.fetch_fuyao("a_share_auction_snapshot", {"thscodes": ",".join(missing)}), timeout=4)
            stored, rejected = [], {}
            for event in normalize_fuyao_auction(data, observed_at):
                raw = event["raw"]
                price = optional_float(raw.get("auction_price"))
                unmatched = optional_float(raw.get("auction_unmatched"))
                # The snapshot carries no date: it is today's only when it is final and its
                # pre-close equals the plan's previous close (yesterday's row has the day before).
                previous_close = optional_float(((plans.get(event["ts_code"]) or {}).get("levels") or {}).get("close"))
                pre_close = optional_float(raw.get("pre_close_price"))
                if str(raw.get("data_status") or "") != "final" or previous_close is None or pre_close is None \
                        or abs(pre_close - previous_close) > max(0.011, previous_close * 0.001):
                    rejected[event["ts_code"]] = f"{raw.get('data_status')}:pre_close={pre_close} vs {previous_close}"
                    continue
                self.market_book.store_auction(event["ts_code"], observed_at, {
                    "amount": optional_float(raw.get("auction_amount")), "price": price,
                    "pct": optional_float(raw.get("auction_pct")), "open": optional_float(raw.get("open_price")),
                    "volume_lot": optional_float(raw.get("auction_volume")),
                    "seal_amount": round(unmatched * 100 * price, 2) if unmatched is not None and price else None,
                    "turnover_pct": optional_float(raw.get("auction_turnover_pct")),
                    "status": raw.get("data_status"), "final": True, "source": "fuyao_auction_0925",
                })
                stored.append(event["ts_code"])
            return {"status": "completed" if stored else "not_ready", "stored": len(stored), "requested": len(missing),
                    "rejected": dict(list(rejected.items())[:5]), "rejected_count": len(rejected)}
        except Exception as error:  # noqa: BLE001 - the proxy stays in force; the scan never waits on this
            return {"status": "failed", "error": self.deps.safe_error(str(error), 200)}

    async def refresh_sectors(self, watches: list[dict[str, Any]]) -> dict[str, Any]:
        """Per-sector limit-up counts for today's teacher plans from the stored limit-up pool."""
        from .teacher_review_repository import latest_limit_up_pool
        try:
            observed_at = self.deps.now()
            sectors = sorted({str((plan.get("params") or {}).get("sector"))
                              for _symbol, plan in self.plans_today(watches, observed_at)
                              if (plan.get("params") or {}).get("sector")})
            if not sectors:
                return {"status": "no_sector_plans"}
            refreshed = self.market_book.sector_refreshed_at
            if refreshed is not None and (observed_at - refreshed).total_seconds() < 5 \
                    and refreshed.astimezone(SHANGHAI).date() == observed_at.astimezone(SHANGHAI).date():
                return {"status": "fresh"}
            day_start = datetime.combine(observed_at.astimezone(SHANGHAI).date(), time(0), tzinfo=SHANGHAI)
            snapshot_at, rows = await self.deps.run_database(
                lambda: latest_limit_up_pool(self.deps.database, since=day_start), timeout_seconds=10)
            counts = count_sector_limit_ups(rows, sectors)
            self.market_book.store_sectors(observed_at, snapshot_at, counts)
            return {"status": "completed", "snapshot_at": str(snapshot_at), "counts": {k: v["count"] for k, v in counts.items()}}
        except Exception as error:  # noqa: BLE001 - an unknown count only keeps the sector gate closed
            return {"status": "failed", "error": self.deps.safe_error(str(error), 200)}

    async def rehydrate_tape(self) -> dict[str, Any]:
        """Once per process and day: reload the snapshot tape from the stored watch tape.

        A restart (a deploy, or the session guard after a database stall) used to
        empty the in-memory tape, so plans without minute context had no 5-minute
        trend for five minutes and raised "data missing".  Never raises.

        A restart *before* the open reads an empty 40-minute window, so the reload
        only settles once it has actually found samples; otherwise it retries on the
        next scan, still behind the 60-second throttle.
        """
        from .teacher_review_rules import trading_lookback_start
        from .watch_scan_tape import read_tape_prices
        now = self.deps.now()
        day = now.astimezone(SHANGHAI).date()
        state = self._tape_rehydration
        if state.get("day") == day and state.get("loaded"):
            return {"status": "done", "loaded": state.get("loaded")}
        last = state.get("attempt_at")
        if last is not None and (now - last).total_seconds() < 60:
            return {"status": "throttled"}
        state["attempt_at"] = now
        since = trading_lookback_start(now, 40 * 60)

        def read() -> list[tuple[datetime, str, float]]:
            with self.deps.database.transaction() as connection:
                return read_tape_prices(connection, since, now)

        try:
            samples = await self.deps.run_database(read, timeout_seconds=15)
            loaded = self.tape.rehydrate(samples)
        except Exception as error:  # noqa: BLE001 - the tape rebuilds from live scans meanwhile
            return {"status": "failed", "error": self.deps.safe_error(str(error), 200)}
        state.update({"day": day, "loaded": loaded})
        return {"status": "completed", "samples": len(samples), "loaded": loaded, "since": since.isoformat()}

    async def refresh_divergence(self, watches: list[dict[str, Any]]) -> dict[str, Any]:
        """Intraday 30/60-minute divergence for today's divergence plans, at most once a minute.

        One blocking-executor slot runs the few K-line reads in sequence under a
        4-second budget; anything unfinished is retried on the next scan and the
        rules keep the previous (or pre-session) result meanwhile.  Never raises.
        """
        from .teacher_review_plan import divergence_status
        from .teacher_review_service import period_bars_through
        try:
            if not self.deps.longhu_configured():
                return {"status": "disabled"}
            observed_at = self.deps.now()
            due = self.divergence_book.due(divergence_plan_symbols(watches, observed_at), observed_at)
            due = due[:DIVERGENCE_MAX_SYMBOLS]
            if not due:
                return {"status": "fresh"}

            def fetch() -> dict[tuple[str, str], Any]:
                source, deadline, fetched = self.deps.longhu_source(), monotonic() + 4.0, {}
                for symbol in due:
                    for period in ("30", "60"):
                        if monotonic() > deadline:
                            return fetched
                        try:
                            fetched[(symbol, period)] = source.stock_period_bars(symbol, period, 120)
                        except Exception as error:  # noqa: BLE001 - one symbol never stops the others
                            fetched[(symbol, period)] = f"{type(error).__name__}: {str(error)[:160]}"
                return fetched

            today = observed_at.astimezone(SHANGHAI).date()
            today_text = today.strftime("%Y%m%d")

            async def today_bars(symbol: str, period: str) -> list[dict[str, Any]] | None:
                # Longhu's K-line only covers completed sessions; Tencent's carries
                # today's bars (true open/high/low, the newest still forming).
                try:
                    bars = await asyncio.wait_for(self.deps.tencent_period_bars(symbol, period, 20), timeout=4)
                except Exception:  # noqa: BLE001 - history alone is still a valid (older) read
                    return None
                return [bar for bar in bars if str(bar["bar_time"]).startswith(today_text)]

            keys = [(symbol, period) for symbol in due for period in ("30", "60")]
            fetched, *today_rows = await asyncio.gather(
                self.deps.run_vendor_blocking(fetch, timeout_seconds=6),
                *(today_bars(symbol, period) for symbol, period in keys))
            today_by_key = dict(zip(keys, today_rows))
            refreshed, errors = [], {}
            for symbol in due:
                rows = {period: fetched.get((symbol, period)) for period in ("30", "60")}
                if all(isinstance(value, list) for value in rows.values()):
                    statuses = {}
                    for period, history in rows.items():
                        today_part = today_by_key.get((symbol, period))
                        merged = {str(bar["bar_time"]): bar for bar in history if not str(bar["bar_time"]).startswith(today_text)}
                        merged.update({str(bar["bar_time"]): bar for bar in today_part or []})
                        status = divergence_status(period_bars_through([merged[k] for k in sorted(merged)], today),
                                                   "longhuvip_kline+tencent_today" if today_part is not None else "longhuvip_kline")
                        status["today_bars"] = None if today_part is None else len(today_part)
                        statuses[period] = status
                    self.divergence_book.store(symbol, observed_at, statuses)
                    refreshed.append(symbol)
                else:
                    errors[symbol] = next((str(value) for value in rows.values() if isinstance(value, str)), "deadline")
            return {"status": "completed" if not errors else "partial", "refreshed": refreshed, "errors": errors}
        except Exception as error:  # noqa: BLE001 - a divergence refresh must never block the scan
            return {"status": "failed", "error": self.deps.safe_error(str(error), 200)}


__all__ = ["TeacherContextDependencies", "TeacherIntradayContext", "optional_float"]
