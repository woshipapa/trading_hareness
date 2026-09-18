"""Always-on collector for the token-free public evidence sources.

One loop, many cadences.  Each source has its own interval and active window
(news all day, the tape only in session), a bounded "already seen" memory so
an unchanged page costs no database round trip, and throttled provider-health
bookkeeping (the owner database is 52ms away; health is written on a state
change or at most every ten minutes per provider/capability).

Nothing here decides anything: every row is research evidence with its own
provenance, and a source failure only degrades that source.
"""

from __future__ import annotations

import asyncio
import time as time_module
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ..derived.market_sentiment import market_sentiment_snapshot
from ..sources import eastmoney_hot_rank, eastmoney_ztb, investor_qa, news_flash
from ..sources.fuyao_evidence import fetch_all_pool_pages


CN_TZ = ZoneInfo("Asia/Shanghai")
HEALTH_REFRESH_SECONDS = 600
SENTIMENT_PROVIDER_KEY = "derived_market_sentiment"


@dataclass(frozen=True)
class SourceCadence:
    key: str
    interval_seconds: int
    #: ``session`` (continuous auction days only), ``day`` (07:00-23:30 every
    #: day) or ``always``.
    window: str


CADENCES: dict[str, SourceCadence] = {item.key: item for item in (
    SourceCadence("news_flash", 90, "always"),
    SourceCadence("investor_qa", 600, "day"),
    SourceCadence("stock_changes", 180, "session"),
    SourceCadence("hot_rank", 600, "session"),
    SourceCadence("board_changes", 900, "session"),
    SourceCadence("sentiment", 300, "session"),
)}
OFF_HOURS_NEWS_INTERVAL = 600


class SeenIds:
    """Bounded insertion-ordered memory of identities already persisted."""

    def __init__(self, capacity: int = 5000) -> None:
        self.capacity = capacity
        self._items: OrderedDict[str, None] = OrderedDict()

    def unseen(self, keys: Sequence[str]) -> set[str]:
        return {key for key in keys if key not in self._items}

    def add(self, keys: Sequence[str]) -> None:
        for key in keys:
            self._items[key] = None
            self._items.move_to_end(key)
        while len(self._items) > self.capacity:
            self._items.popitem(last=False)


@dataclass
class CollectorDeps:
    persist_events: Callable[[str, list[dict[str, Any]]], Awaitable[int]]
    persist_observations: Callable[[str, str, list[dict[str, Any]]], Awaitable[int]]
    record_health: Callable[[str, str, bool, int, int | None, str | None], Awaitable[None]]
    fuyao_fetch: Callable[[str, dict[str, Any]], Awaitable[Mapping[str, Any]]] | None = None
    fuyao_snapshot: Callable[[], Awaitable[tuple[list[dict[str, Any]], dict[str, Any]]]] | None = None
    previous_turnover_total: Callable[[date], Awaitable[float | None]] | None = None
    concept_membership: Callable[[], Awaitable[dict[str, set[str]] | None]] | None = None
    fetch_flashes: Mapping[str, Callable[[], Awaitable[list[dict[str, Any]]]]] = field(
        default_factory=lambda: dict(news_flash.FETCHERS))
    log: Callable[[str], None] = print


@dataclass
class CollectorState:
    last_run: dict[str, float] = field(default_factory=dict)
    seen: dict[str, SeenIds] = field(default_factory=dict)
    health: dict[tuple[str, str], tuple[bool, float]] = field(default_factory=dict)
    concept_codes: dict[str, str] = field(default_factory=dict)

    def seen_for(self, key: str, capacity: int = 5000) -> SeenIds:
        return self.seen.setdefault(key, SeenIds(capacity))


def in_window(window: str, now: datetime, *, session_open: bool) -> bool:
    local = now.astimezone(CN_TZ).time()
    if window == "always":
        return True
    if window == "day":
        return time(7, 0) <= local <= time(23, 30)
    return session_open and (time(9, 15) <= local <= time(11, 31) or time(12, 59) <= local <= time(15, 1))


def due(state: CollectorState, cadence: SourceCadence, now: datetime, *, session_open: bool, monotonic: float) -> bool:
    if not in_window(cadence.window, now, session_open=session_open):
        return False
    interval = cadence.interval_seconds
    if cadence.key == "news_flash":
        local = now.astimezone(CN_TZ).time()
        if not time(7, 0) <= local <= time(23, 59):
            interval = OFF_HOURS_NEWS_INTERVAL
    last = state.last_run.get(cadence.key)
    return last is None or monotonic - last >= interval


async def _health(deps: CollectorDeps, state: CollectorState, provider: str, capability: str, ok: bool,
                  rows: int, latency_ms: int | None, error: str | None) -> None:
    key = (provider, capability)
    previous = state.health.get(key)
    now = time_module.monotonic()
    if previous is not None and previous[0] == ok and now - previous[1] < HEALTH_REFRESH_SECONDS:
        return
    state.health[key] = (ok, now)
    try:
        await deps.record_health(provider, capability, ok, rows, latency_ms, error)
    except Exception as health_error:  # noqa: BLE001 - health is bookkeeping, never fatal
        deps.log(f"public evidence health write failed: {str(health_error)[:200]}")


async def _timed(coroutine: Awaitable[Any]) -> tuple[Any, int]:
    started = time_module.monotonic()
    result = await coroutine
    return result, int((time_module.monotonic() - started) * 1000)


async def capture_news(deps: CollectorDeps, state: CollectorState, now: datetime) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for provider, fetch in deps.fetch_flashes.items():
        try:
            flashes, latency = await _timed(fetch())
            seen = state.seen_for(f"news:{provider}")
            fresh_ids = seen.unseen([item["flash_id"] for item in flashes])
            fresh = [flash for flash in flashes if flash["flash_id"] in fresh_ids]
            stored = 0
            if fresh:
                stored = await deps.persist_observations(
                    provider, "news_flash", [news_flash.flash_observation(flash, now) for flash in fresh])
                events = [event for flash in fresh for event in news_flash.flash_events(flash)]
                if events:
                    await deps.persist_events(provider, events)
                seen.add([flash["flash_id"] for flash in fresh])
            result[provider] = {"status": "completed", "received": len(flashes), "new": len(fresh), "stored": stored}
            await _health(deps, state, provider, "news_flash", True, len(flashes), latency, None)
        except Exception as error:  # noqa: BLE001 - one site outage must not stop the others
            result[provider] = {"status": "failed", "error": str(error)[:200]}
            await _health(deps, state, provider, "news_flash", False, 0, None, str(error))
    return result


async def capture_investor_qa(deps: CollectorDeps, state: CollectorState, now: datetime) -> dict[str, Any]:
    result: dict[str, Any] = {}
    sources: dict[str, Callable[[], Awaitable[list[dict[str, Any]]]]] = {
        "cninfo_irm": lambda: investor_qa.fetch_cninfo(page_size=50),
        "sse_einteract": lambda: investor_qa.fetch_sse(now, page_size=30),
    }
    for provider, fetch in sources.items():
        try:
            rows, latency = await _timed(fetch())
            seen = state.seen_for(f"qa:{provider}")
            fresh_ids = seen.unseen([row["qa_id"] for row in rows])
            fresh = [row for row in rows if row["qa_id"] in fresh_ids]
            stored = await deps.persist_events(provider, investor_qa.qa_events(fresh)) if fresh else 0
            seen.add([row["qa_id"] for row in fresh])
            result[provider] = {"status": "completed", "received": len(rows), "new": len(fresh), "stored": stored}
            await _health(deps, state, provider, "investor_qa", True, len(rows), latency, None)
        except Exception as error:  # noqa: BLE001
            result[provider] = {"status": "failed", "error": str(error)[:200]}
            await _health(deps, state, provider, "investor_qa", False, 0, None, str(error))
    return result


async def capture_stock_changes(deps: CollectorDeps, state: CollectorState, now: datetime) -> dict[str, Any]:
    provider = eastmoney_ztb.PROVIDER_KEY
    try:
        rows, latency = await _timed(eastmoney_ztb.fetch_stock_changes())
        trade_date = now.astimezone(CN_TZ).date()
        events = eastmoney_ztb.stock_change_events(rows, trade_date)
        seen = state.seen_for(f"changes:{trade_date.isoformat()}", 50_000)
        fresh_ids = seen.unseen([event["event_identity_key"] for event in events])
        fresh = [event for event in events if event["event_identity_key"] in fresh_ids]
        stored = await deps.persist_events(provider, fresh) if fresh else 0
        seen.add([event["event_identity_key"] for event in fresh])
        await _health(deps, state, provider, "stock_change", True, len(rows), latency, None)
        return {"status": "completed", "received": len(rows), "new": len(fresh), "stored": stored}
    except Exception as error:  # noqa: BLE001
        await _health(deps, state, provider, "stock_change", False, 0, None, str(error))
        return {"status": "failed", "error": str(error)[:200]}


async def capture_hot_ranks(deps: CollectorDeps, state: CollectorState, now: datetime) -> dict[str, Any]:
    provider = eastmoney_hot_rank.PROVIDER_KEY
    result: dict[str, Any] = {}
    for kind in eastmoney_hot_rank.RANK_LISTS:
        capability = f"hot_rank_{kind}"
        try:
            rows, latency = await _timed(eastmoney_hot_rank.fetch_rank_list(kind))
            observations = [{**row, "ts_code": row["symbol"], "effective_at": now.isoformat(),
                             "available_at": now.isoformat()} for row in rows]
            stored = await deps.persist_observations(provider, capability, observations) if observations else 0
            result[kind] = {"status": "completed", "received": len(rows), "stored": stored}
            await _health(deps, state, provider, capability, True, len(rows), latency, None)
        except Exception as error:  # noqa: BLE001
            result[kind] = {"status": "failed", "error": str(error)[:200]}
            await _health(deps, state, provider, capability, False, 0, None, str(error))
    return result


async def capture_board_changes(deps: CollectorDeps, state: CollectorState, now: datetime) -> dict[str, Any]:
    provider = eastmoney_ztb.PROVIDER_KEY
    try:
        (boards, stamp), latency = await _timed(eastmoney_ztb.fetch_board_changes(300))
        observation = {"ts_code": None, "upstream_stamp": stamp, "boards": boards,
                       "effective_at": now.isoformat(), "available_at": now.isoformat()}
        stored = await deps.persist_observations(provider, "board_change_snapshot", [observation]) if boards else 0
        await _health(deps, state, provider, "board_change_snapshot", True, len(boards), latency, None)
        return {"status": "completed", "received": len(boards), "stored": stored}
    except Exception as error:  # noqa: BLE001
        await _health(deps, state, provider, "board_change_snapshot", False, 0, None, str(error))
        return {"status": "failed", "error": str(error)[:200]}


async def _concept_quotes(deps: CollectorDeps, state: CollectorState) -> dict[str, dict[str, Any]]:
    if deps.fuyao_fetch is None:
        return {}
    if not state.concept_codes:
        catalog = await deps.fuyao_fetch("ths_index_list", {"tag": "cn_concept"})
        state.concept_codes = {str(item.get("thscode")).upper(): str(item.get("name") or "")
                               for item in catalog.get("item") or [] if isinstance(item, Mapping) and item.get("thscode")}
    quotes: dict[str, dict[str, Any]] = {}
    codes = sorted(state.concept_codes)
    for offset in range(0, len(codes), 100):
        data = await deps.fuyao_fetch("ths_index_prices_snapshot", {"thscodes": ",".join(codes[offset:offset + 100])})
        for item in data.get("item") or []:
            if isinstance(item, Mapping) and item.get("thscode"):
                code = str(item["thscode"]).upper()
                quotes[code] = {**dict(item), "index_name": state.concept_codes.get(code)}
    return quotes


async def build_sentiment(deps: CollectorDeps, state: CollectorState, now: datetime) -> dict[str, Any]:
    """Assemble one indicator record from Fuyao pools, the tape and EM's
    previous-day pool (which carries yesterday's board count directly)."""
    if deps.fuyao_fetch is None or deps.fuyao_snapshot is None:
        raise RuntimeError("Fuyao is not configured; sentiment needs its pools and tape")
    pools = {}
    for capability in ("a_share_limit_up_pool", "a_share_limit_break_pool", "a_share_limit_down_pool"):
        pools[capability] = (await fetch_all_pool_pages(deps.fuyao_fetch, capability)).get("item") or []
    snapshot_rows, _meta = await deps.fuyao_snapshot()
    trade_date = now.astimezone(CN_TZ).date()
    previous = await eastmoney_ztb.fetch_pool("previous_limit_up", trade_date)
    previous_rows = [{"symbol": row["symbol"], "board_count": row.get("previous_board_count") or 1} for row in previous]
    previous_total = await deps.previous_turnover_total(trade_date) if deps.previous_turnover_total else None
    membership = await deps.concept_membership() if deps.concept_membership else None
    try:
        index_quotes = await _concept_quotes(deps, state)
    except Exception as error:  # noqa: BLE001 - strength is optional
        deps.log(f"concept quotes unavailable: {str(error)[:200]}")
        index_quotes = {}
    reading = market_sentiment_snapshot(
        limit_up_rows=[{**row, "symbol": row.get("thscode")} for row in pools["a_share_limit_up_pool"]],
        broken_rows=[{**row, "symbol": row.get("thscode")} for row in pools["a_share_limit_break_pool"]],
        limit_down_rows=[{**row, "symbol": row.get("thscode")} for row in pools["a_share_limit_down_pool"]],
        previous_limit_up_rows=previous_rows, snapshot_rows=snapshot_rows,
        previous_turnover_total=previous_total, index_quotes=index_quotes, concept_membership=membership,
    )
    return {**reading, "trade_date": trade_date.isoformat(), "observed_at": now.isoformat(),
            "sources": {"pools": "fuyao_ths", "tape": "fuyao_ths_all_a_snapshot",
                        "previous_limit_up": "eastmoney_ztb", "concept_quotes": "fuyao_ths" if index_quotes else None,
                        "concept_membership": "fuyao_ths_concept" if membership else None}}


async def capture_sentiment(deps: CollectorDeps, state: CollectorState, now: datetime) -> dict[str, Any]:
    try:
        reading, latency = await _timed(build_sentiment(deps, state, now))
        stored = await deps.persist_observations(SENTIMENT_PROVIDER_KEY, "market_sentiment_snapshot", [{
            **reading, "ts_code": None, "effective_at": now.isoformat(), "available_at": now.isoformat(),
        }])
        await _health(deps, state, SENTIMENT_PROVIDER_KEY, "market_sentiment_snapshot", True, 1, latency, None)
        return {"status": "completed", "stored": stored, "limit_up_count": reading["limit_up_count"],
                "seal_rate": reading["seal_rate"]}
    except Exception as error:  # noqa: BLE001
        await _health(deps, state, SENTIMENT_PROVIDER_KEY, "market_sentiment_snapshot", False, 0, None, str(error))
        return {"status": "failed", "error": str(error)[:200]}


CAPTURES: dict[str, Callable[[CollectorDeps, CollectorState, datetime], Awaitable[dict[str, Any]]]] = {
    "news_flash": capture_news, "investor_qa": capture_investor_qa, "stock_changes": capture_stock_changes,
    "hot_rank": capture_hot_ranks, "board_changes": capture_board_changes, "sentiment": capture_sentiment,
}


async def run_once(deps: CollectorDeps, state: CollectorState, now: datetime, *, session_open: bool,
                   enabled: Mapping[str, bool] | None = None) -> dict[str, Any]:
    results: dict[str, Any] = {}
    monotonic = time_module.monotonic()
    for key, cadence in CADENCES.items():
        if enabled is not None and not enabled.get(key, True):
            continue
        if not due(state, cadence, now, session_open=session_open, monotonic=monotonic):
            continue
        state.last_run[key] = monotonic
        try:
            results[key] = await CAPTURES[key](deps, state, now)
        except Exception as error:  # noqa: BLE001 - a capture bug must not stop the loop
            results[key] = {"status": "failed", "error": str(error)[:200]}
    return results


async def run_loop(
    deps: CollectorDeps,
    *,
    session_open: Callable[[datetime], Awaitable[bool]],
    enabled: Mapping[str, bool] | None = None,
    tick_seconds: int = 30,
) -> None:
    state = CollectorState()
    while True:
        now = datetime.now(timezone.utc)
        try:
            active = await session_open(now)
        except Exception:  # noqa: BLE001 - calendar outage keeps news flowing
            active = False
        results = await run_once(deps, state, now, session_open=bool(active), enabled=enabled)
        degraded = {key: value for key, value in results.items()
                    if isinstance(value, dict) and value.get("status") == "failed"}
        if degraded:
            deps.log(f"public evidence capture degraded: {str(degraded)[:500]}")
        await asyncio.sleep(max(10, tick_seconds))


__all__ = [
    "CADENCES", "CAPTURES", "CollectorDeps", "CollectorState", "SENTIMENT_PROVIDER_KEY", "SeenIds",
    "SourceCadence", "build_sentiment", "due", "in_window", "run_loop", "run_once",
]
