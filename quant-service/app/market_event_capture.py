"""Capture Fuyao all-A auction, limit-pool and attention evidence.

This module contains no strategy decisions.  It converts the documented
Fuyao payloads into the existing ``market_events`` evidence shape (and, for
rankings, timed raw observations) and leaves the provider call, cadence and
database executor to the application.

Two provider limits shape the requests (both measured 2026-09-18):

* pools are paginated with a default page of 50 -- a day with 77 limit-ups
  returned only the first 50 until the pages were walked;
* ``thscodes`` accepts at most 100 codes, and one delisted or non-equity code
  fails the whole batch ("Unknown thscode: 000003.SZ").
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any, Awaitable, Callable, Mapping, MutableMapping, Sequence
from zoneinfo import ZoneInfo

from .datasources.sources.fuyao_evidence import (
    MAX_CODES_PER_REQUEST, anomaly_events, auction_benchmark_events, equity_codes, fetch_all_pool_pages,
    fetch_code_batches, rank_observations,
)

CN_TZ = ZoneInfo("Asia/Shanghai")

CAPTURE_CAPABILITIES: tuple[str, ...] = (
    "a_share_limit_up_pool", "a_share_limit_break_pool", "a_share_limit_down_pool", "a_share_limit_up_ladder",
)
PAGED_POOLS = frozenset({"a_share_limit_up_pool", "a_share_limit_break_pool", "a_share_limit_down_pool"})
#: (capability, params, minimum seconds between captures) for attention data.
ATTENTION_CAPTURES: tuple[tuple[str, dict[str, Any], int], ...] = (
    ("a_share_hot_stock_list", {"period": "day"}, 600),
    ("a_share_skyrocket_list", {"period": "day"}, 600),
    ("a_share_anomaly_analysis_list", {}, 300),
)


def _items(data: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    value = (data or {}).get("item")
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def normalize_fuyao_events(capability: str, data: Mapping[str, Any] | None,
                           observed_at: datetime) -> list[dict[str, Any]]:
    """Normalize one Fuyao capability while preserving its raw item."""
    events: list[dict[str, Any]] = []
    if capability == "a_share_limit_up_ladder":
        boards = (data or {}).get("boards")
        # Some responses put boards under each dated item; accept both shapes.
        dated = _items(data)
        board_sets = [boards] if isinstance(boards, dict) else [item.get("boards") for item in dated]
        for board_set in board_sets:
            if not isinstance(board_set, dict):
                continue
            for bucket, values in board_set.items():
                for item in values if isinstance(values, list) else []:
                    if not isinstance(item, dict) or not item.get("thscode"):
                        continue
                    symbol = str(item["thscode"]).upper()
                    board_num = item.get("board_num")
                    events.append({
                        "ts_code": symbol, "event_type": "limit_chain",
                        "published_at": observed_at.isoformat(),
                        "title": f"连板链：{item.get('name') or symbol} {board_num or bucket}",
                        "url": None, "event_identity_key": f"fuyao_ths:limit_chain:{symbol}:{observed_at.astimezone(CN_TZ).date().isoformat()}:{board_num or bucket}",
                        "raw": {"capability": capability, "bucket": bucket, **item},
                    })
        return events

    event_type, label = {
        "a_share_limit_up_pool": ("limit_up_pool", "涨停池"),
        "a_share_limit_break_pool": ("limit_open_pool", "炸板池"),
        "a_share_limit_down_pool": ("limit_down_pool", "跌停池"),
    }.get(capability, (None, None))
    if event_type is None:
        return []
    for item in _items(data):
        symbol = str(item.get("thscode") or "").upper()
        if not symbol:
            continue
        events.append({
            "ts_code": symbol, "event_type": event_type,
            "published_at": observed_at.isoformat(),
            "title": f"{label}：{item.get('name') or symbol}", "url": None,
            # Include the observed minute: pool membership changes during the
            # session must remain separate evidence, not an upserted snapshot.
            "event_identity_key": f"fuyao_ths:{event_type}:{symbol}:{observed_at.strftime('%Y%m%d%H%M')}",
            "raw": {"capability": capability, **item},
        })
    return events


def normalize_fuyao_auction(data: Mapping[str, Any] | None, observed_at: datetime) -> list[dict[str, Any]]:
    """Normalize the final all-A auction snapshot (one daily identity/name)."""
    events: list[dict[str, Any]] = []
    for item in _items(data):
        symbol = str(item.get("thscode") or "").upper()
        if not symbol:
            continue
        events.append({
            "ts_code": symbol, "event_type": "auction_final",
            "published_at": observed_at.isoformat(),
            "title": f"收盘集合竞价：{item.get('name') or symbol}", "url": None,
            "event_identity_key": f"fuyao_ths:auction_final:{symbol}:{observed_at.strftime('%Y%m%d')}",
            "raw": {"capability": "a_share_auction_snapshot", **item,
                    "auction_phase": (data or {}).get("auction_phase"),
                    "data_status": (data or {}).get("data_status")},
        })
    return events


def _due(state: MutableMapping[str, Any] | None, key: str, now: datetime, interval_seconds: int) -> bool:
    if state is None:
        return False
    last = state.get(key)
    return not isinstance(last, datetime) or (now - last).total_seconds() >= interval_seconds


async def capture(
    observed_at: datetime,
    *,
    fetch: Callable[[str, dict[str, Any]], Awaitable[Mapping[str, Any]]],
    persist: Callable[[str, list[dict[str, Any]]], Awaitable[int]],
    include_auction: bool = False,
    auction_symbols: Sequence[str] = (),
    persist_observations: Callable[[str, str, list[dict[str, Any]]], Awaitable[int]] | None = None,
    persist_health: Callable[[str, str, int, str | None], Awaitable[None]] | None = None,
    state: MutableMapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch and persist the bounded event set, continuing after one failure.

    ``state`` (owned by the caller's loop) enables the slower attention
    captures and the once-a-day auction benchmark; without it only the pools
    and the requested auction run, as before.
    """
    results: dict[str, Any] = {"status": "completed", "capabilities": {}, "stored": 0}

    async def report_health(provider: str, capability: str, rows: int, error: str | None) -> None:
        if persist_health is None:
            return
        try:
            await persist_health(provider, capability, rows, error)
        except Exception as health_error:  # noqa: BLE001 - health must not stop evidence capture
            results["status"] = "partial"
            results.setdefault("health_errors", []).append(str(health_error)[:240])

    for capability in CAPTURE_CAPABILITIES:
        try:
            if capability in PAGED_POOLS:
                data = await fetch_all_pool_pages(fetch, capability)
            else:
                data = await fetch(capability, {})
            rows = normalize_fuyao_events(capability, data, observed_at)
            stored = await persist("fuyao_ths", rows) if rows else 0
            results["capabilities"][capability] = {"status": "completed", "received": len(rows), "stored": stored}
            results["stored"] += stored
            await report_health("fuyao_ths", capability, len(rows), None)
        except Exception as error:  # noqa: BLE001 - one pool outage must not stop the others
            results["status"] = "partial"
            results["capabilities"][capability] = {"status": "failed", "error": str(error)[:240]}
            await report_health("fuyao_ths", capability, 0, str(error)[:240])

    local = observed_at.astimezone(CN_TZ)
    if state is not None and time(9, 26) <= local.time() <= time(9, 45) \
            and state.get("auction_benchmark_date") != local.date().isoformat():
        try:
            rows = auction_benchmark_events(await fetch("a_share_auction_short_term_benchmark", {}), observed_at)
            stored = await persist("fuyao_ths", rows) if rows else 0
            if rows:
                state["auction_benchmark_date"] = local.date().isoformat()
            results["capabilities"]["a_share_auction_short_term_benchmark"] = {"status": "completed", "received": len(rows), "stored": stored}
            results["stored"] += stored
            await report_health("fuyao_ths", "a_share_auction_short_term_benchmark", len(rows), None)
        except Exception as error:  # noqa: BLE001
            results["status"] = "partial"
            results["capabilities"]["a_share_auction_short_term_benchmark"] = {"status": "failed", "error": str(error)[:240]}
            await report_health("fuyao_ths", "a_share_auction_short_term_benchmark", 0, str(error)[:240])

    for capability, params, interval in ATTENTION_CAPTURES:
        if not _due(state, capability, observed_at, interval):
            continue
        assert state is not None
        state[capability] = observed_at
        try:
            data = await fetch(capability, dict(params))
            if capability == "a_share_anomaly_analysis_list":
                rows = anomaly_events(data, observed_at)
                stored = await persist("fuyao_ths", rows) if rows else 0
            else:
                rows = rank_observations(capability, data, observed_at)
                stored = await persist_observations("fuyao_ths", capability, rows) if rows and persist_observations else 0
            results["capabilities"][capability] = {"status": "completed", "received": len(rows), "stored": stored}
            results["stored"] += stored
            await report_health("fuyao_ths", capability, len(rows), None)
        except Exception as error:  # noqa: BLE001
            results["status"] = "partial"
            results["capabilities"][capability] = {"status": "failed", "error": str(error)[:240]}
            await report_health("fuyao_ths", capability, 0, str(error)[:240])

    if include_auction and auction_symbols:
        batches, dropped, failures = await fetch_code_batches(fetch, "a_share_auction_snapshot", auction_symbols)
        received = stored = 0
        for _codes, data in batches:
            rows = normalize_fuyao_auction(data, observed_at)
            received += len(rows)
            stored += await persist("fuyao_ths", rows) if rows else 0
        results["auction"] = {
            "status": "completed" if not failures else "partial", "requested": len(equity_codes(auction_symbols)),
            "received": received, "stored": stored, "dropped_codes": dropped[:50], "failures": failures[:10],
        }
        results["stored"] += stored
        await report_health("fuyao_ths", "a_share_auction_snapshot", received,
                           None if not failures else "; ".join(failures[:3]))
        if failures:
            results["status"] = "partial"
    return results


__all__ = [
    "ATTENTION_CAPTURES", "CAPTURE_CAPABILITIES", "MAX_CODES_PER_REQUEST", "capture", "equity_codes",
    "fetch_all_pool_pages", "fetch_code_batches", "normalize_fuyao_auction", "normalize_fuyao_events",
]
