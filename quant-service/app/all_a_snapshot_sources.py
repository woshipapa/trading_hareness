"""Projections, agreement measurements and the fallback chain for all-A snapshot sources.

The main source is Fuyao/THS. The TDX snapshot fills a minute the main source fails, labelled with
its own source: nothing here relabels a TDX row as Fuyao, and whether a filled minute is decision
eligible is what the catalog binding says when the minute is read.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from statistics import median
from typing import Any, Iterable, Mapping

from .datasources.catalog import BINDINGS
from .datasources.contracts import CapabilityEvidence
from .error_detail import safe_error_detail
from .fuyao_provider import FUYAO_PROVIDER_KEY, FuyaoProviderError
from .intraday_cross_section import SharedAsyncSnapshot
from .public_provider_rate_limits import PublicProviderRateLimited, acquire_public_provider_slot


TDX_PROVIDER_KEY = "tdx_public"
TDX_SNAPSHOT_SOURCE = "tdx_public_all_a_snapshot"
_SNAPSHOT_CAPABILITY = "quote.all_a_snapshot"
_TDX_BINDING = next(item for item in BINDINGS
                    if item.source == TDX_PROVIDER_KEY and item.capability == _SNAPSHOT_CAPABILITY)
_TDX_VOLUME_FACTOR = _TDX_BINDING.spec.unit_factors["volume"]
TDX_SNAPSHOT_MIN_COMPLETE_SHARE = 0.95
TDX_SNAPSHOT_ROSTER_FLOOR = 5000
#: How long a caller waits for one TDX all-A read (the shadow capture and the fallback chain).
TDX_READ_TIMEOUT_SECONDS = 20.0
#: Two readers of the TDX snapshot within this many seconds share one read.
TDX_SHARED_READ_TTL_SECONDS = 5.0
FALLBACK_REASON_MAX_CHARS = 300


class AllASnapshotFallbackError(FuyaoProviderError):
    """The main source failed and the TDX snapshot did not fill the minute.

    It is a ``FuyaoProviderError`` raised from the Fuyao one, so every caller that handles a
    failed main read handles this exactly as it did before the chain existed.
    """

    def __init__(self, fuyao_error: FuyaoProviderError, fallback_reason: str) -> None:
        self.fallback_reason = safe_error_detail(fallback_reason, FALLBACK_REASON_MAX_CHARS)
        super().__init__(
            safe_error_detail(f"{fuyao_error}; {TDX_PROVIDER_KEY} fallback: {self.fallback_reason}", 600),
            code=fuyao_error.code, request_id=fuyao_error.request_id,
        )


def tdx_snapshot_completeness(priced_rows: int, warnings: Iterable[str],
                              roster_total: int | None) -> dict[str, Any]:
    no_trade_rows = next(
        (int(warning.removeprefix("no_trade_rows="))
         for warning in warnings if warning.startswith("no_trade_rows=")),
        0,
    )
    roster = max(priced_rows + no_trade_rows, roster_total or 0, TDX_SNAPSHOT_ROSTER_FLOOR)
    share = priced_rows / roster
    return {
        "priced_rows": priced_rows,
        "no_trade_rows": no_trade_rows,
        "roster_total": roster_total,
        "roster": roster,
        "share": share,
        "complete": roster_total is not None and share >= TDX_SNAPSHOT_MIN_COMPLETE_SHARE,
    }


def project_tdx_snapshot_rows(rows: Iterable[Mapping[str, Any]], observed_at: datetime) -> list[dict[str, Any]]:
    """Project native TDX all-A rows into the Fuyao snapshot row shape."""
    observed = observed_at.isoformat()
    projected: list[dict[str, Any]] = []
    for row in rows:
        price = row["price"]
        if price <= 0:
            continue
        pre_close = row["pre_close"]
        volume = row["volume_lots"] * _TDX_VOLUME_FACTOR
        turnover = row["amount"]
        projected.append({
            "symbol": row["symbol"],
            "price": price,
            "pct_change": row["pct_change"],
            "turnover": turnover,
            "volume": volume,
            "price_source": TDX_SNAPSHOT_SOURCE,
            "price_observed_at": observed,
            "raw": {
                "thscode": row["symbol"],
                "ticker": row["code"],
                "volume": volume,
                "turnover": turnover,
                "last_price": price,
                "price_change": round(price - pre_close, 2),
                "price_change_ratio_pct": row["pct_change"],
                "open_price": row["open"],
                "high_price": row["high"],
                "low_price": row["low"],
                "prev_price": pre_close,
            },
        })
    return projected


def tdx_snapshot_decision_eligible() -> bool:
    """What the catalog binding of the TDX all-A snapshot says now; no binding is not eligible."""
    return any(item.decision_eligible for item in BINDINGS
               if item.source == TDX_PROVIDER_KEY and item.capability == _SNAPSHOT_CAPABILITY)


def tdx_snapshot_metadata(*, completeness: Mapping[str, Any], fallback_reason: str, available_at: datetime,
                          warnings: Iterable[str] = ()) -> dict[str, Any]:
    """The metadata of a TDX-filled minute, in the shape ``fuyao_provider.all_a_snapshot_rows`` returns.

    The server sends no snapshot timestamp, so the freshness is ``unknown`` and the time the read
    returned is kept as ``available_at`` instead of being passed off as an upstream time.
    """
    return {
        "status": "unknown", "freshness_reason": "no_upstream_timestamp", "age_seconds": None,
        "max_age_seconds": None, "available_at": available_at.isoformat(),
        "source": TDX_SNAPSHOT_SOURCE, "provider_key": TDX_PROVIDER_KEY,
        "decision_eligible": tdx_snapshot_decision_eligible(),
        "fallback_for": FUYAO_PROVIDER_KEY,
        "fallback_reason": safe_error_detail(fallback_reason, FALLBACK_REASON_MAX_CHARS),
        "scope": "all_a_cross_section", "cross_sectional": True,
        "semantics": "all_a_price_volume_turnover_snapshot_no_main_flow", "upstream_timestamp_ms": None,
        "total": completeness["priced_rows"] + completeness["no_trade_rows"],
        "matched_rows": completeness["priced_rows"],
        "completeness": dict(completeness), "warnings": list(warnings),
    }


def snapshot_decision_eligible(status: Any) -> bool:
    """Whether a row of the snapshot with this status may be acted on.

    The answer is the snapshot's own ``decision_eligible`` and nothing else: the shared cache
    puts its ``status`` and age over the supplier's, so a minute the fallback filled reads
    ``fresh`` and its freshness says nothing about its source. A status that carries the flag
    must carry ``True``. One without the key comes from a caller that predates the flag and is
    treated as before; both sources of the chain always set it.
    """
    if not isinstance(status, Mapping) or "decision_eligible" not in status:
        return True
    return status["decision_eligible"] is True


def fallback_provenance(status: Any) -> dict[str, Any]:
    """What a consumer reports beside its result when another source filled the minute.

    Nothing for a minute the main source served, so its results are what they were.
    """
    if not isinstance(status, Mapping) or not status.get("fallback_for"):
        return {}
    return {"snapshot_source": status.get("source"), "snapshot_provider": status.get("provider_key"),
            "fallback_for": status["fallback_for"]}


def main_source_reference(
    remembered: tuple[datetime, list[dict[str, Any]]] | None,
) -> tuple[datetime, list[dict[str, Any]]] | None:
    """The remembered minute when the main source served it, otherwise nothing.

    The level1 capture remembers whatever it stored, a TDX-filled minute included. The shadow
    comparison must not measure TDX against itself, so a minute whose rows do not say
    ``fuyao_ths`` is no reference.
    """
    if remembered is None:
        return None
    rows = remembered[1]
    if not rows or (rows[0].get("snapshot_metadata") or {}).get("provider_key") != FUYAO_PROVIDER_KEY:
        return None
    return remembered


def shared_tdx_snapshot(
    fetch: Callable[[], Awaitable[CapabilityEvidence | list[dict[str, Any]]]], *,
    clock: Callable[[], float],
    acquire_slot: Callable[..., Awaitable[None]] = acquire_public_provider_slot,
) -> SharedAsyncSnapshot[CapabilityEvidence | list[dict[str, Any]]]:
    """The one reader of the TDX all-A snapshot, for the shadow capture and the fallback chain.

    Every ``tdx_protocol`` call opens its own connection and the access rule is one connection a
    host, so the two callers must never read side by side: a read in flight is joined, and a
    result is shared for ``TDX_SHARED_READ_TTL_SECONDS``. The read takes its ``tdx_public`` slot
    without waiting; a refused slot reaches each caller as ``PublicProviderRateLimited``. A caller
    keeps its own task around ``get()`` and consumes that task's late exception.
    """
    async def read_once() -> CapabilityEvidence | list[dict[str, Any]]:
        await acquire_slot(TDX_PROVIDER_KEY, max_wait_seconds=0)
        return await fetch()

    return SharedAsyncSnapshot(read_once, ttl_seconds=TDX_SHARED_READ_TTL_SECONDS, clock=clock)


def _consume_late_exception(task: asyncio.Future[Any]) -> None:
    if task.cancelled():
        return
    try:
        task.exception()
    except BaseException:
        pass


async def read(
    fuyao: Callable[[], Awaitable[tuple[list[dict[str, Any]], dict[str, Any]]]],
    tdx: Callable[[], Awaitable[CapabilityEvidence | list[dict[str, Any]]]],
    roster_total: Callable[[], Awaitable[int | None]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The all-A snapshot: Fuyao first; when Fuyao fails as a provider, one TDX read fills the minute.

    Only a ``FuyaoProviderError`` starts the fallback; anything else propagates. A TDX error, a
    refused slot, a read that outlives ``TDX_READ_TIMEOUT_SECONDS`` and a snapshot that is not
    complete against ``roster_total`` (the main source's stored upstream total; unknown is
    incomplete) all raise ``AllASnapshotFallbackError`` from the Fuyao error: an untrusted fill
    is a missing minute.

    The caller's wait is bounded, the read is not cut: its task keeps awaiting ``tdx`` (in the
    service a shared reader, so a later caller joins the same read) and its late outcome is
    consumed here. The one read thread ends on its own. ``tdx_protocol.call_sync`` gives every
    socket operation 5 s (the connect and each ``recv``; no deadline spans a whole exchange) and
    starts no further host once 15 s have passed since the call began. A host costs at most 20 s
    when it answers nothing: a connect and a silent handshake of 5 s each, for ``login_one`` and
    again for ``legacy_3``; 5 s when the connect itself fails. A read that finds no usable host
    therefore ends within 15 + 20 = 35 s. Only a server that keeps sending inside every 5 s
    window lasts longer: a snapshot is about 70 pages, and a connection is cut at 120.
    """
    try:
        return await fuyao()
    except FuyaoProviderError as fuyao_error:
        task = asyncio.ensure_future(tdx())
        task.add_done_callback(_consume_late_exception)
        # ``asyncio.wait`` leaves the task alone on a timeout, as ``wait_for(shield(task))`` does,
        # and has no shield for Python 3.14 to report the read's late failure through.
        done, _ = await asyncio.wait({task}, timeout=TDX_READ_TIMEOUT_SECONDS)
        if not done:
            raise AllASnapshotFallbackError(
                fuyao_error, f"tdx_read_timeout:{TDX_READ_TIMEOUT_SECONDS:g}s") from fuyao_error
        try:
            result = task.result()
        except PublicProviderRateLimited as error:
            raise AllASnapshotFallbackError(fuyao_error, str(error)) from fuyao_error
        except Exception as error:  # noqa: BLE001 - any failed fill leaves the Fuyao failure standing
            raise AllASnapshotFallbackError(
                fuyao_error, f"tdx_read_failed:{type(error).__name__}: {error}") from fuyao_error
        if isinstance(result, CapabilityEvidence):
            rows, warnings, available_at = result.rows, result.warnings, result.available_at_max
        else:
            rows, warnings, available_at = result, (), None
        available_at = available_at or datetime.now(timezone.utc)
        try:
            total = await roster_total()
        except Exception:  # noqa: BLE001 - without its roster the gate fails closed
            total = None
        completeness = tdx_snapshot_completeness(len(rows), warnings, total)
        if not completeness["complete"]:
            raise AllASnapshotFallbackError(
                fuyao_error,
                "tdx_snapshot_incomplete:priced_rows={priced_rows},no_trade_rows={no_trade_rows},"
                "roster_total={roster_total},share={share:.3f}".format(**completeness),
            ) from fuyao_error
        return project_tdx_snapshot_rows(rows, available_at), tdx_snapshot_metadata(
            completeness=completeness, fallback_reason=str(fuyao_error) or type(fuyao_error).__name__,
            available_at=available_at, warnings=warnings,
        )


def _share(values: Iterable[bool]) -> float | None:
    values = list(values)
    return sum(values) / len(values) if values else None


def _ratio_values(rows: Iterable[tuple[float, float]]) -> list[float]:
    return [right / left for left, right in rows if left != 0]


def compare(fuyao_rows: Iterable[Mapping[str, Any]], tdx_rows: Iterable[Mapping[str, Any]],
            seconds_apart: float) -> dict[str, Any]:
    """Measure the agreement of two all-A snapshots without applying a verdict."""
    fuyao = {row["symbol"]: row for row in fuyao_rows}
    tdx = {row["symbol"]: row for row in tdx_rows}
    common = sorted(fuyao.keys() & tdx.keys())
    pairs = [(fuyao[symbol], tdx[symbol]) for symbol in common]

    price_gaps = [(abs(tdx_row["price"] - fuyao_row["price"]),
                   abs(tdx_row["price"] - fuyao_row["price"]) / abs(fuyao_row["price"]), symbol)
                  for symbol, (fuyao_row, tdx_row) in zip(common, pairs)]
    pct_gaps = [abs(tdx_row["pct_change"] - fuyao_row["pct_change"])
                for fuyao_row, tdx_row in pairs]
    volume_ratios = _ratio_values((fuyao_row["volume"], tdx_row["volume"])
                                  for fuyao_row, tdx_row in pairs)
    turnover_ratios = _ratio_values((fuyao_row["turnover"], tdx_row["turnover"])
                                    for fuyao_row, tdx_row in pairs)
    largest_price_gap = max(price_gaps, default=None, key=lambda item: item[1])

    return {
        "symbols_in_both": len(common),
        "only_in_fuyao": sorted(fuyao.keys() - tdx.keys()),
        "only_in_tdx": sorted(tdx.keys() - fuyao.keys()),
        "price": {
            "within_one_tick": _share(gap[0] <= 0.01 for gap in price_gaps),
            "within_0_1_pct": _share(gap[1] <= 0.001 for gap in price_gaps),
            "within_0_5_pct": _share(gap[1] <= 0.005 for gap in price_gaps),
            "largest_relative_gap": largest_price_gap[1] if largest_price_gap else None,
            "largest_relative_gap_symbol": largest_price_gap[2] if largest_price_gap else None,
        },
        "pct_change": {"largest_absolute_gap": max(pct_gaps, default=None)},
        "volume": {
            "median_ratio": median(volume_ratios) if volume_ratios else None,
            "within_1_pct": _share(abs(ratio - 1) <= 0.01 for ratio in volume_ratios),
        },
        "turnover": {
            "median_ratio": median(turnover_ratios) if turnover_ratios else None,
            "within_1_pct": _share(abs(ratio - 1) <= 0.01 for ratio in turnover_ratios),
        },
        "seconds_apart": seconds_apart,
    }


__all__ = [
    "AllASnapshotFallbackError", "FALLBACK_REASON_MAX_CHARS", "TDX_PROVIDER_KEY", "TDX_READ_TIMEOUT_SECONDS",
    "TDX_SHARED_READ_TTL_SECONDS", "TDX_SNAPSHOT_MIN_COMPLETE_SHARE", "TDX_SNAPSHOT_ROSTER_FLOOR",
    "TDX_SNAPSHOT_SOURCE", "compare", "fallback_provenance", "main_source_reference", "project_tdx_snapshot_rows",
    "read", "shared_tdx_snapshot", "snapshot_decision_eligible", "tdx_snapshot_completeness",
    "tdx_snapshot_decision_eligible", "tdx_snapshot_metadata",
]
