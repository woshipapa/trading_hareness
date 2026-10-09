"""THS board catalogue and constituents from Fuyao, refreshed after the close.

Tushare's ``ths_index``/``ths_member`` fed the THS boards until it was retired
(decision 0005); since then the catalogue route, the concept-member backfill
and its loop failed.  Fuyao is THS's own data service: ``ths_index_list``
lists the concept, industry and region indices by tag, and
``ths_index_constituents`` returns an index's complete constituent list in
one call, so a board needs one request instead of paged ``ths_member`` calls.

What changes against the Tushare rows:

* each tag is its own taxonomy -- ``fuyao_ths_concept``, ``fuyao_ths_industry``
  and ``fuyao_ths_region`` -- never written into ``ths_concept_flow`` or
  ``ths_index_*``, whose rows stay as history;
* Fuyao gives no in/out dates, so a membership starts on the session it was
  first observed, with ``known_at`` the observation: a refresh during a
  session counts for intraday readers only from the next session;
* only the concept, industry and region tags have a taxonomy here; the
  ``S``/``ST``/``BB`` types (feature, style, broad-based) are reported
  unavailable rather than mapped to a guessed tag.

The process-wide Fuyao limiter is non-blocking and shared with the session
capture and the close archive, so requests here are spaced, a refused start
is retried after a pause, and a batch that still cannot start a request
stops and leaves the rest due (``deferred``) instead of failing boards.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from . import fuyao_provider
from .datasources.http import ashare_symbol
from .error_detail import safe_error_detail
from .fuyao_ths_membership_repository import (
    PROVIDER_KEY, boards_due, boards_page, listed_counts, persist_catalog, persist_member_snapshot,
    record_member_failure, refresh_progress,
)
from .public_provider_rate_limits import PublicProviderRateLimited
from .sector_membership_repository import observed_exchange_date


#: Fuyao tag -> (taxonomy, label); the order is the refresh order.
FUYAO_THS_TAXONOMIES: dict[str, tuple[str, str]] = {
    "cn_concept": ("fuyao_ths_concept", "同花顺概念（Fuyao）"),
    "industry": ("fuyao_ths_industry", "同花顺行业（Fuyao）"),
    "region": ("fuyao_ths_region", "同花顺地域（Fuyao）"),
}
#: The Tushare ``ths_index`` types the catalogue route accepts, by Fuyao tag.
INDEX_TYPE_TAGS: dict[str, str] = {"N": "cn_concept", "I": "industry", "R": "region"}
CONCEPT_TAG = "cn_concept"
INDEX_CODE = re.compile(r"\d{6}\.TI")
#: One request a second is the whole default Fuyao budget (60/min); 1.5 s
#: leaves a third of it to the session capture and the close archive.
REQUEST_SPACING_SECONDS = 1.5
RATE_LIMIT_RETRY_SECONDS = 2.0
RATE_LIMIT_RETRIES = 3
#: 融资融券 alone lists ~3,900 members; the executor's 10s default is for small writes.
MEMBER_PERSIST_TIMEOUT_SECONDS = 60.0


class FuyaoRateLimitedError(RuntimeError):
    """The shared limiter kept refusing a start after the bounded retries."""


async def _provider_fetch(capability: str, params: dict[str, Any]) -> Mapping[str, Any]:
    # Late-bound so a patched provider is the one called.
    return await fuyao_provider.fetch(capability, params)


def _configured() -> bool:
    return fuyao_provider.configured()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class FuyaoThsMembershipDependencies:
    run_database: Callable[..., Awaitable[Any]]
    database: Any
    fetch: Callable[[str, dict[str, Any]], Awaitable[Mapping[str, Any]]] = _provider_fetch
    configured: Callable[[], bool] = _configured
    now_utc: Callable[[], datetime] = _now_utc
    sleep: Callable[[float], Awaitable[Any]] = field(default=asyncio.sleep)


class _LoopLock:
    """One refresh at a time per event loop.

    The THS loop, the all-board loop and the routes select boards from the
    same durable receipts; serialising them keeps two callers from fetching
    the same board and from splitting the Fuyao budget between them.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None

    def get(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._loop is not loop or self._lock is None:
            self._loop, self._lock = loop, asyncio.Lock()
        return self._lock


_REFRESH_LOCK = _LoopLock()


def _rate_limited(error: BaseException) -> bool:
    return isinstance(error.__cause__, PublicProviderRateLimited) or str(error).startswith("rate_limited:")


class _Pacer:
    """Spaces this refresh's Fuyao requests and absorbs a refused start."""

    def __init__(self, deps: FuyaoThsMembershipDependencies) -> None:
        self._deps = deps
        self.requests = 0

    async def fetch(self, capability: str, params: dict[str, Any]) -> Mapping[str, Any]:
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            if self.requests:
                await self._deps.sleep(REQUEST_SPACING_SECONDS)
            self.requests += 1
            try:
                return await self._deps.fetch(capability, params)
            except Exception as error:
                if not _rate_limited(error):
                    raise
                if attempt == RATE_LIMIT_RETRIES:
                    raise FuyaoRateLimitedError(f"rate_limited:{PROVIDER_KEY}") from error
                await self._deps.sleep(RATE_LIMIT_RETRY_SECONDS)
        raise FuyaoRateLimitedError(f"rate_limited:{PROVIDER_KEY}")


def index_catalog_rows(data: Mapping[str, Any] | None) -> tuple[list[tuple[str, str]], int]:
    """``(code, name)`` for every named ``NNNNNN.TI`` index, and the rows skipped."""
    boards: dict[str, str] = {}
    items = (data or {}).get("item")
    rows = [row for row in items if isinstance(row, Mapping)] if isinstance(items, list) else []
    for row in rows:
        code = str(row.get("thscode") or "").strip().upper()
        name = str(row.get("name") or "").strip()
        if INDEX_CODE.fullmatch(code) and name:
            boards.setdefault(code, name)
    return sorted(boards.items()), len(rows) - len(boards)


def constituent_members(data: Mapping[str, Any] | None, index_code: str) -> dict[str, dict[str, Any]]:
    """A-share equity constituents keyed by symbol, raw row kept for evidence."""
    members: dict[str, dict[str, Any]] = {}
    items = (data or {}).get("item")
    for row in items if isinstance(items, list) else []:
        if not isinstance(row, Mapping):
            continue
        symbol = ashare_symbol(row.get("thscode"))
        if symbol:
            members[symbol] = {"name": row.get("name"), "thscode": row.get("thscode"), "index_code": index_code}
    return members


def _base(**fields: Any) -> dict[str, Any]:
    return {"source": PROVIDER_KEY, "provider": PROVIDER_KEY, **fields}


def _unconfigured(**fields: Any) -> dict[str, Any]:
    return _base(status="blocked", reason="Fuyao API key is not configured; THS boards were not refreshed", **fields)


def _trade_date_notice(requested: date | None, refresh_date: date) -> dict[str, str]:
    if requested is None or requested == refresh_date:
        return {}
    return {"notice": f"Fuyao serves current constituents only: this refreshed {refresh_date}'s snapshot, "
                      f"which counts from {refresh_date}; {requested}'s membership was not and cannot be changed."}


async def refresh_catalog(tag: str, deps: FuyaoThsMembershipDependencies, pacer: _Pacer,
                          listed_on: date) -> dict[str, Any]:
    """List one tag and stamp every listed board; nothing is written on failure."""
    taxonomy_key, label = FUYAO_THS_TAXONOMIES[tag]
    result = {"taxonomy_key": taxonomy_key, "tag": tag, "sectors": 0}
    try:
        data = await pacer.fetch("ths_index_list", {"tag": tag})
    except FuyaoRateLimitedError as error:
        return {**result, "status": "blocked", "reason": str(error)}
    except Exception as error:  # noqa: BLE001 - reported per tag; no catalogue row is touched
        return {**result, "status": "failed", "reason": safe_error_detail(str(error), 300)}
    boards, skipped = index_catalog_rows(data)
    if not boards:
        return {**result, "status": "blocked", "reason": "ths_index_list returned no THS index rows", "skipped_rows": skipped}
    stored = await deps.run_database(persist_catalog, deps.database, taxonomy_key, label, tag, boards, listed_on)
    return {**result, "status": "completed", "sectors": stored, "skipped_rows": skipped}


async def refresh_boards(taxonomy_key: str, boards: list[dict[str, Any]], deps: FuyaoThsMembershipDependencies,
                         pacer: _Pacer) -> list[dict[str, Any]]:
    """Fetch and apply each board; a refused start defers the rest of the batch.

    ``known_at`` is the moment each response arrived, never the batch start:
    a membership must not look known before it was.
    """
    results: list[dict[str, Any]] = []
    for board in boards:
        sector_key, label = str(board["sector_key"]), str(board.get("label") or board["sector_key"])
        item: dict[str, Any] = {"taxonomy_key": taxonomy_key, "sector_key": sector_key, "label": label}
        try:
            data = await pacer.fetch("ths_index_constituents", {"thscode": sector_key})
        except FuyaoRateLimitedError as error:
            results.append({**item, "status": "deferred", "members": 0, "reason": str(error)})
            break
        except Exception as error:  # noqa: BLE001 - one board must not end the refresh
            detail = safe_error_detail(str(error), 300)
            await deps.run_database(record_member_failure, deps.database, taxonomy_key, sector_key,
                                    observed_exchange_date(deps.now_utc()), detail)
            results.append({**item, "status": "failed", "members": 0, "error": detail})
            continue
        outcome = await deps.run_database(
            persist_member_snapshot, deps.database, taxonomy_key, sector_key,
            constituent_members(data, sector_key), deps.now_utc(), timeout_seconds=MEMBER_PERSIST_TIMEOUT_SECONDS,
        )
        results.append({**item, "status": outcome["state"], "members": outcome["members"],
                        "opened": outcome["opened"], "closed": outcome["closed"]})
    return results


def _attempted(results: list[dict[str, Any]]) -> int:
    """Boards a page actually reached; a deferred one is due again."""
    return sum(1 for item in results if item["status"] != "deferred")


def _status(results: list[dict[str, Any]], catalogs: list[dict[str, Any]]) -> str:
    settled = [item for item in results if item["status"] in {"completed", "empty"}]
    troubled = [item for item in [*results, *catalogs] if item["status"] not in {"completed", "empty"}]
    if troubled and not settled and all(item["status"] in {"blocked", "deferred"} for item in troubled):
        return "blocked"
    return "partial" if troubled else "completed"


async def sync_catalog(request: Any, deps: FuyaoThsMembershipDependencies) -> dict[str, Any]:
    """``POST /market/sectors/sync`` for one ``ths_index`` type, served by Fuyao.

    A ``resume`` member batch continues the day's refresh, so it lists the tag
    only if today has no listing yet; any other call lists it afresh.
    """
    tag = INDEX_TYPE_TAGS.get(str(request.index_type))
    if tag is None:
        return _base(status="unavailable", index_type=request.index_type, taxonomy_key=None, sectors=0,
                     reason="only THS concept (N), industry (I) and region (R) indices are refreshed from Fuyao")
    taxonomy_key = FUYAO_THS_TAXONOMIES[tag][0]
    if not deps.configured():
        return _unconfigured(index_type=request.index_type, taxonomy_key=taxonomy_key, sectors=0)
    async with _REFRESH_LOCK.get():
        refresh_date = observed_exchange_date(deps.now_utc())
        pacer = _Pacer(deps)
        if request.sync_members and request.resume:
            catalogs = await _ensure_catalogs((tag,), deps, pacer, refresh_date, False)
        else:
            catalogs = [await refresh_catalog(tag, deps, pacer, refresh_date)]
        if catalogs and catalogs[0]["status"] != "completed":
            return _base(index_type=request.index_type, refresh_date=str(refresh_date), **catalogs[0])
        results: list[dict[str, Any]] = []
        total = catalogs[0]["sectors"] if catalogs else 0
        if request.sync_members:
            if request.resume:
                boards, total = await deps.run_database(boards_due, deps.database, taxonomy_key, refresh_date,
                                                        request.member_limit)
            else:
                boards, total = await deps.run_database(boards_page, deps.database, taxonomy_key, refresh_date,
                                                        request.member_offset, request.member_limit)
            results = await refresh_boards(taxonomy_key, boards, deps, pacer)
    end = request.member_offset + _attempted(results)
    return _base(
        status=_status(results, []), taxonomy_key=taxonomy_key, index_type=request.index_type, fuyao_tag=tag,
        sectors=total, refresh_date=str(refresh_date), listed_now=bool(catalogs),
        member_offset=request.member_offset, resume=request.resume, member_results=results,
        skipped_non_member_codes=catalogs[0]["skipped_rows"] if catalogs else 0,
        next_member_offset=end if request.sync_members and not request.resume and end < total else None,
    )


async def _ensure_catalogs(tags: tuple[str, ...], deps: FuyaoThsMembershipDependencies, pacer: _Pacer,
                           refresh_date: date, force: bool) -> list[dict[str, Any]]:
    """List each tag at most once a day unless ``force``; stop at a refused start."""
    listed = await deps.run_database(
        listed_counts, deps.database, [FUYAO_THS_TAXONOMIES[tag][0] for tag in tags], refresh_date,
    )
    catalogs: list[dict[str, Any]] = []
    for tag in tags:
        if force or not listed.get(FUYAO_THS_TAXONOMIES[tag][0]):
            catalogs.append(await refresh_catalog(tag, deps, pacer, refresh_date))
            if catalogs[-1]["status"] == "blocked" and catalogs[-1].get("reason", "").startswith("rate_limited:"):
                break
    return catalogs


async def sync_concept_members(request: Any, deps: FuyaoThsMembershipDependencies) -> dict[str, Any]:
    """``POST /market/sectors/concepts/members/sync``: one bounded concept page."""
    taxonomy_key = FUYAO_THS_TAXONOMIES[CONCEPT_TAG][0]
    if not deps.configured():
        return _unconfigured(taxonomy_key=taxonomy_key, member_results=[])
    async with _REFRESH_LOCK.get():
        refresh_date = observed_exchange_date(deps.now_utc())
        pacer = _Pacer(deps)
        catalogs = await _ensure_catalogs((CONCEPT_TAG,), deps, pacer, refresh_date, request.refresh_flow_catalog)
        if any(item["status"] != "completed" for item in catalogs):
            return _base(status="blocked", taxonomy_key=taxonomy_key, refresh_date=str(refresh_date),
                         reason="unable to list Fuyao THS concepts", catalog=catalogs[0], member_results=[])
        if request.resume:
            boards, total = await deps.run_database(boards_due, deps.database, taxonomy_key, refresh_date,
                                                    request.member_limit)
        else:
            boards, total = await deps.run_database(boards_page, deps.database, taxonomy_key, refresh_date,
                                                    request.member_offset, request.member_limit)
        results = await refresh_boards(taxonomy_key, boards, deps, pacer)
    end = request.member_offset + _attempted(results)
    return _base(
        status=_status(results, catalogs), taxonomy_key=taxonomy_key, refresh_date=str(refresh_date),
        trade_date=str(refresh_date), total_concepts=total, member_offset=request.member_offset,
        member_limit=request.member_limit, resume=request.resume, member_results=results,
        catalogs=catalogs, next_member_offset=end if not request.resume and end < total else None,
        **_trade_date_notice(request.trade_date, refresh_date),
    )


async def run_batch(request: Any, deps: FuyaoThsMembershipDependencies) -> dict[str, Any]:
    """One bounded refresh batch: concepts, then industries, then regions.

    Each tag is listed once a day (or on ``refresh_flow_catalog``); then up to
    ``batch_size`` boards without a settled receipt for today are fetched.
    The loop calling this after the close finishes the day's refresh in
    batches, and a restart resumes from the receipts.
    """
    taxonomy_keys = [taxonomy_key for taxonomy_key, _label in FUYAO_THS_TAXONOMIES.values()]
    if not deps.configured():
        return _unconfigured(taxonomy_keys=taxonomy_keys, member_results=[],
                             progress={"completed_or_empty": 0, "failed": 0, "remaining": None})
    async with _REFRESH_LOCK.get():
        refresh_date = observed_exchange_date(deps.now_utc())
        pacer = _Pacer(deps)
        catalogs = await _ensure_catalogs(tuple(FUYAO_THS_TAXONOMIES), deps, pacer, refresh_date,
                                          request.refresh_flow_catalog)
        results: list[dict[str, Any]] = []
        budget = request.batch_size
        for taxonomy_key in taxonomy_keys:
            if budget <= 0 or any(item["status"] == "deferred" for item in results) or any(
                    str(item.get("reason", "")).startswith("rate_limited:") for item in catalogs):
                break
            boards, _listed = await deps.run_database(boards_due, deps.database, taxonomy_key, refresh_date, budget)
            results.extend(await refresh_boards(taxonomy_key, boards, deps, pacer))
            budget -= len(boards)
        progress = await deps.run_database(refresh_progress, deps.database, taxonomy_keys, refresh_date)
    return _base(
        status=_status(results, catalogs), refresh_date=str(refresh_date), trade_date=str(refresh_date),
        taxonomy_keys=taxonomy_keys, batch_size=request.batch_size, provider_requests=pacer.requests,
        catalogs=catalogs, member_results=results,
        total_concepts=progress[taxonomy_keys[0]]["listed"], taxonomies=progress,
        progress={key: sum(item[key] for item in progress.values()) for key in ("completed_or_empty", "failed", "remaining")},
        **_trade_date_notice(request.trade_date, refresh_date),
    )


__all__ = [
    "CONCEPT_TAG", "FUYAO_THS_TAXONOMIES", "FuyaoRateLimitedError", "FuyaoThsMembershipDependencies",
    "INDEX_TYPE_TAGS", "REQUEST_SPACING_SECONDS", "constituent_members", "index_catalog_rows",
    "refresh_boards", "refresh_catalog", "run_batch", "sync_catalog", "sync_concept_members",
]
