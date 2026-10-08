"""Bounded public-quote capture for an explicit intraday watchlist."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from .platform.evidence_contracts import materialize_evidence_status
from .intraday_price_priority import fresh_price_rows


def _parse_upstream_timestamp(value: Any, *, timezone_hint: timezone = timezone.utc) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone_hint)
    text = str(value).strip()
    if text.isdigit():
        number = int(text)
        if len(text) >= 13:
            number /= 1000
        if 0 < number < 4_000_000_000:
            return datetime.fromtimestamp(number, tz=timezone.utc)
    compact = "".join(character for character in text if character.isdigit())
    if len(compact) >= 14:
        try:
            return datetime.strptime(compact[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone_hint)
        except ValueError:
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone_hint)


def eastmoney_flow_freshness(
    rows: list[dict[str, Any]], observed_at: datetime, *, max_age_seconds: float = 45.0,
) -> dict[str, Any]:
    """Classify upstream flow freshness without treating request completion as freshness."""
    timestamps = [
        _parse_upstream_timestamp(
            row.get("upstream_observed_at") or row.get("source_available_at")
            or row.get("timestamp") or row.get("trade_time")
            or (row.get("raw") or {}).get("f124"),
            timezone_hint=observed_at.tzinfo or timezone.utc,
        )
        for row in rows
    ]
    timestamps = [item for item in timestamps if item is not None]
    if not rows:
        return {"status": "empty", "request_status": "completed", "freshness_status": "unknown",
                "freshness_reason": "no_rows", "age_seconds": None, "max_age_seconds": max_age_seconds}
    if not timestamps:
        return {"status": "unknown", "request_status": "completed", "freshness_status": "unknown",
                "freshness_reason": "upstream_timestamp_missing", "age_seconds": None,
                "max_age_seconds": max_age_seconds}
    newest = max(timestamps).astimezone(timezone.utc)
    age_seconds = (observed_at.astimezone(timezone.utc) - newest).total_seconds()
    if age_seconds < -5:
        freshness_status, reason = "invalid", "future_timestamp"
    elif age_seconds > max_age_seconds:
        freshness_status, reason = "stale", "age_exceeded"
    else:
        freshness_status, reason = "fresh", "within_slo"
    return {"status": freshness_status, "request_status": "completed", "freshness_status": freshness_status,
            "freshness_reason": reason, "age_seconds": round(max(age_seconds, 0.0), 3),
            "upstream_observed_at": newest.isoformat(), "max_age_seconds": max_age_seconds}


async def _batched_provider_fetch(
    fetch: Callable[..., Awaitable[list[dict[str, Any]]]],
    symbols: list[str],
    *,
    batch_size: int,
) -> list[dict[str, Any]]:
    """Fetch an expanded observation pool using audited provider batch sizes.

    Providers retain their independently verified request cap (currently 40)
    while the user-facing observation pool can be expanded to 100.  Partial
    batch failures are preserved as partial evidence; only an all-batch
    failure is raised so the caller can activate its existing fallback path.
    """
    unique = list(dict.fromkeys(str(symbol).upper() for symbol in symbols))
    if not unique:
        return []
    batches = [unique[index:index + batch_size] for index in range(0, len(unique), batch_size)]
    results = await asyncio.gather(
        *(fetch(batch, max_symbols=batch_size) for batch in batches),
        return_exceptions=True,
    )
    rows: list[dict[str, Any]] = []
    errors: list[BaseException] = []
    for result in results:
        if isinstance(result, BaseException):
            errors.append(result)
        elif isinstance(result, list):
            rows.extend(result)
    if rows or not errors:
        return rows
    raise errors[0]


@dataclass(frozen=True)
class WatchQuoteCapture:
    quotes: dict[str, dict[str, Any]]
    all_a_rows: list[dict[str, Any]]
    all_a_snapshot_status: dict[str, Any]
    fresh_watch_rows: list[dict[str, Any]]
    sina_watch_rows: list[dict[str, Any]]
    eastmoney_watch_flow_rows: list[dict[str, Any]]
    eastmoney_watch_flow_status: dict[str, Any]
    derived_flow_status: dict[str, Any]
    latency_ms: int
    licensed_watch_rows: list[dict[str, Any]]
    licensed_watch_status: dict[str, Any]


@dataclass(frozen=True)
class WatchQuoteCaptureDependencies:
    now: Callable[[], float]
    all_a_snapshot: Callable[[], Awaitable[tuple[list[dict[str, Any]], dict[str, Any]]]]
    tencent_watch_quotes: Callable[..., Awaitable[list[dict[str, Any]]]]
    sina_quotes: Callable[[list[str]], Awaitable[list[dict[str, Any]]]]
    eastmoney_watch_flows: Callable[..., Awaitable[list[dict[str, Any]]]]
    watch_flow_reference: Callable[[list[str], datetime], Awaitable[dict[str, dict[str, Any]]]]
    derive_flow_metrics: Callable[..., dict[str, dict[str, float]]]
    apply_derived_flow_metrics: Callable[
        [dict[str, dict[str, Any]], dict[str, dict[str, float]]], dict[str, dict[str, str]]
    ]
    derived_flow_divergence: Callable[..., dict[str, Any]]
    quote_from_all_a: Callable[[dict[str, Any]], dict[str, Any] | None]
    merge_eastmoney_flows: Callable[[dict[str, dict[str, Any]], list[dict[str, Any]]], Any]
    annotate_percentiles: Callable[[dict[str, dict[str, Any]]], Any]
    annotate_flow_provenance: Callable[[dict[str, dict[str, Any]], dict[str, Any]], Any]
    merge_watch_prices: Callable[[dict[str, dict[str, Any]], list[dict[str, Any]]], Any]
    merge_sina_prices: Callable[[dict[str, dict[str, Any]], list[dict[str, Any]]], Any]
    quote_freshness: Callable[[dict[str, Any], datetime, float], dict[str, Any]]
    consume_background_exception: Callable[[Any], Any]
    safe_error: Callable[[str, int], str]
    executor_saturated_error: type[Exception]
    watch_quote_errors: tuple[type[Exception], ...]
    watch_flow_reference_errors: tuple[type[Exception], ...]
    all_a_snapshot_errors: tuple[type[Exception], ...]
    licensed_watch_quotes: Callable[
        [list[str]], Awaitable[tuple[list[dict[str, Any]], dict[str, Any]]]
    ] | None = None
    merge_licensed_prices: Callable[
        [dict[str, dict[str, Any]], list[dict[str, Any]]], Any
    ] | None = None
    licensed_quote_errors: tuple[type[Exception], ...] = ()
    # The shared Longhu gateway may fan out a bounded watch basket on the
    # owner side.  Four seconds caused false "unavailable" states at the open.
    licensed_quote_timeout_seconds: float = 12.0


async def _apply_derived_flow_metrics(
    quotes: dict[str, dict[str, Any]], reference_task: "asyncio.Task[dict[str, dict[str, Any]]]",
    observed_at: datetime, dependencies: WatchQuoteCaptureDependencies,
) -> dict[str, Any]:
    """Overlay the licensed derived metrics over the public Eastmoney values.

    This runs after the Eastmoney merge on purpose: the derived value wins
    where it exists, and any field it cannot derive - always including
    ``main_net_inflow``, which no licensed route supplies - keeps whatever the
    public endpoint returned.  A reference read that fails or exceeds the scan
    budget degrades to exactly today's Eastmoney-only behaviour.
    """
    try:
        reference = await asyncio.wait_for(asyncio.shield(reference_task), timeout=2.0)
    except (asyncio.TimeoutError, *dependencies.watch_flow_reference_errors) as error:
        return materialize_evidence_status(
            "fuyao_ths_derived_watch_flow",
            {"status": "unavailable", "error": dependencies.safe_error(str(error), 300)},
        )
    # The derivation needs cumulative volume, carried by the all-A snapshot.
    # When the snapshot supplied none, the derived fields stay unavailable for
    # this scan. (A batched ProMax rt_k volume used to stand in; it went with
    # the Tushare retirement on 2026-10-08, and with no credentials left it
    # raised out of the scan instead of falling back.)
    derived = dependencies.derive_flow_metrics(quotes, reference, observed_at=observed_at)
    sources = dependencies.apply_derived_flow_metrics(quotes, derived)
    divergence = dependencies.derived_flow_divergence(quotes, derived)
    field_counts = {
        field: sum(1 for labels in sources.values() if labels.get(field) in {"fuyao_ths_derived", "longhuvip_volume_derived"})
        for field in ("volume_ratio", "turnover_rate")
    }
    return materialize_evidence_status(
        "fuyao_ths_derived_watch_flow",
        {"status": "fresh" if derived else "unavailable", "age_seconds": 0.0,
         "source": "source_labeled_volume_with_local_float_shares",
         "reference_symbols": len(reference), "derived_symbols": len(derived),
         "derived_field_symbols": field_counts,
         "longhu_derived_field_symbols": {
             field: sum(1 for labels in sources.values() if labels.get(field) == "longhuvip_volume_derived")
             for field in ("volume_ratio", "turnover_rate")
         },
         "native_longhu_field_symbols": {
             field: sum(1 for labels in sources.values() if labels.get(field) == "longhuvip_watch_quote")
             for field in ("volume_ratio", "turnover_rate")
         },
         "volume_sources": sorted({str(quote.get("volume_source") or "fuyao_ths_all_a_snapshot")
                                   for quote in quotes.values() if quote.get("volume") is not None}),
         "main_net_inflow_source": "eastmoney_watch_flow_only_no_licensed_equivalent",
         "eastmoney_agreement": divergence},
    )


async def capture_watch_quotes(
    symbols: list[str], observed_at: datetime, quote_timestamp_slo_seconds: float,
    dependencies: WatchQuoteCaptureDependencies,
) -> WatchQuoteCapture:
    """Capture bounded quote evidence without promoting fallback prices.

    The all-A task is deliberately allowed only a two-second scan budget.  If
    it finishes later its exception is consumed, but it never delays direct
    watch prices or turns a stale cross-section into a decision quote.
    """
    started_at = dependencies.now()
    timeout_or_all_a_errors = (asyncio.TimeoutError, *dependencies.all_a_snapshot_errors)
    all_a_task = asyncio.create_task(dependencies.all_a_snapshot())
    all_a_task.add_done_callback(dependencies.consume_background_exception)
    # This is deliberately a single bounded request for the explicit
    # watchlist, started beside the all-A snapshot.  It is research
    # corroboration only: its values never represent an all-market ranking.
    eastmoney_task = asyncio.create_task(
        _batched_provider_fetch(dependencies.eastmoney_watch_flows, symbols, batch_size=40),
    )
    eastmoney_task.add_done_callback(dependencies.consume_background_exception)
    # Local reference for the derived flow metrics.  It is a small indexed read
    # of already-persisted end-of-day rows, started here so it overlaps the
    # provider calls instead of extending the scan budget.
    reference_task = asyncio.create_task(dependencies.watch_flow_reference(symbols, observed_at))
    reference_task.add_done_callback(dependencies.consume_background_exception)
    licensed_task = (
        asyncio.create_task(dependencies.licensed_watch_quotes(symbols))
        if dependencies.licensed_watch_quotes is not None else None
    )
    if licensed_task is not None:
        licensed_task.add_done_callback(dependencies.consume_background_exception)
    # Tencent remains an independent cross-check, not the preferred price.
    # Start it in parallel so a slow public source cannot delay Longhu's call.
    public_task = asyncio.create_task(_batched_provider_fetch(
        dependencies.tencent_watch_quotes, symbols, batch_size=40,
    ))
    public_task.add_done_callback(dependencies.consume_background_exception)
    try:
        fresh_watch_rows = await asyncio.wait_for(asyncio.shield(public_task), timeout=3.0)
    except (asyncio.TimeoutError, *dependencies.watch_quote_errors):
        fresh_watch_rows = []
    # Non-empty is not the same as complete or fresh. Fill gaps per symbol.
    accepted_public, _public_rejected = fresh_price_rows(
        fresh_watch_rows, symbols=symbols, merge=dependencies.merge_watch_prices,
        freshness=dependencies.quote_freshness, observed_at=observed_at,
        max_age_seconds=quote_timestamp_slo_seconds,
    )
    public_symbols = {str(row.get("ts_code") or row.get("symbol") or "") for row in accepted_public}
    missing_public = [symbol for symbol in symbols if symbol not in public_symbols]
    try:
        sina_watch_rows = await asyncio.wait_for(dependencies.sina_quotes(missing_public), timeout=3.0) if missing_public else []
    except (asyncio.TimeoutError, *dependencies.watch_quote_errors):
        sina_watch_rows = []
    try:
        all_a_rows, all_a_snapshot_status = await asyncio.wait_for(asyncio.shield(all_a_task), timeout=2.0)
    except dependencies.executor_saturated_error as error:
        detail = dependencies.safe_error(str(error), 300)
        all_a_rows, all_a_snapshot_status = [], {"status": "unavailable", "error": detail}
    except timeout_or_all_a_errors as error:
        detail = dependencies.safe_error(str(error), 300)
        all_a_rows, all_a_snapshot_status = [], {"status": "unavailable", "error": detail}
    all_a_snapshot_status = materialize_evidence_status("fuyao_all_a_snapshot", all_a_snapshot_status)
    quotes = {item["symbol"]: item for row in all_a_rows if (item := dependencies.quote_from_all_a(row)) is not None}
    eastmoney_watch_flow_rows: list[dict[str, Any]] = []
    eastmoney_watch_flow_status = materialize_evidence_status(
        "eastmoney_watch_flow", {"status": "unavailable"}, research_confirmation_only=True,
    )
    try:
        eastmoney_watch_flow_rows = await asyncio.wait_for(asyncio.shield(eastmoney_task), timeout=2.0)
    except (asyncio.TimeoutError, *dependencies.watch_quote_errors) as error:
        eastmoney_watch_flow_status["error"] = dependencies.safe_error(str(error), 300)
    else:
        freshness = eastmoney_flow_freshness(eastmoney_watch_flow_rows, observed_at)
        eastmoney_watch_flow_status = materialize_evidence_status(
            "eastmoney_watch_flow",
            {**freshness, "source": "eastmoney_watch_flow_batch",
             "matched_symbols": len(eastmoney_watch_flow_rows)},
            research_confirmation_only=True,
        )
    if all_a_rows and all_a_snapshot_status.get("cross_sectional", True):
        dependencies.annotate_percentiles(quotes)
    dependencies.annotate_flow_provenance(quotes, all_a_snapshot_status)
    if eastmoney_watch_flow_rows:
        dependencies.merge_eastmoney_flows(quotes, eastmoney_watch_flow_rows)
        eastmoney_quotes = {
            str(row.get("ts_code") or ""): quotes[str(row.get("ts_code") or "")]
            for row in eastmoney_watch_flow_rows if str(row.get("ts_code") or "") in quotes
        }
        dependencies.annotate_flow_provenance(eastmoney_quotes, eastmoney_watch_flow_status)
    evaluated_at = observed_at + timedelta(seconds=max(0.0, dependencies.now() - started_at))
    accepted_public, _public_rejected = fresh_price_rows(
        fresh_watch_rows, symbols=symbols, merge=dependencies.merge_watch_prices,
        freshness=dependencies.quote_freshness, observed_at=evaluated_at,
        max_age_seconds=quote_timestamp_slo_seconds,
    )
    dependencies.merge_watch_prices(quotes, accepted_public)
    accepted_sina, _sina_rejected = fresh_price_rows(
        sina_watch_rows, symbols=missing_public, merge=dependencies.merge_sina_prices,
        freshness=dependencies.quote_freshness, observed_at=evaluated_at,
        max_age_seconds=quote_timestamp_slo_seconds,
    )
    for row in accepted_sina:
        symbol = str(row.get("ts_code") or row.get("symbol") or "")
        if symbol in quotes:
            # A stale all-A context price must not suppress a fresh fallback.
            quotes[symbol].pop("price", None)
    dependencies.merge_sina_prices(quotes, accepted_sina)
    licensed_watch_rows: list[dict[str, Any]] = []
    licensed_watch_status = materialize_evidence_status(
        "longhuvip_watch_quote", {"status": "disabled", "requested": len(symbols)},
    )
    if licensed_task is not None:
        try:
            licensed_watch_rows, raw_licensed_status = await asyncio.wait_for(
                asyncio.shield(licensed_task), timeout=max(0.001, dependencies.licensed_quote_timeout_seconds
                    - max(0.0, dependencies.now() - started_at)),
            )
        except (asyncio.TimeoutError, *dependencies.licensed_quote_errors) as error:
            licensed_watch_status = materialize_evidence_status(
                "longhuvip_watch_quote",
                {"status": "unavailable", "requested": len(symbols),
                 "error": dependencies.safe_error(str(error), 300)},
            )
        else:
            licensed_watch_status = materialize_evidence_status(
                "longhuvip_watch_quote", raw_licensed_status,
            )
            if dependencies.merge_licensed_prices is not None:
                evaluated_at = observed_at + timedelta(seconds=max(0.0, dependencies.now() - started_at))
                accepted, rejected = fresh_price_rows(
                    licensed_watch_rows, symbols=symbols, merge=dependencies.merge_licensed_prices,
                    freshness=dependencies.quote_freshness, observed_at=evaluated_at,
                    max_age_seconds=quote_timestamp_slo_seconds,
                )
                dependencies.merge_licensed_prices(quotes, accepted)
                # A licensed row that missed the price freshness SLO still
                # carries the session's pre-close/open and a slightly older
                # cumulative book.  Keep it beside the chosen price for rules
                # that need those fields; it never becomes the price source.
                accepted_symbols = {str(r.get("ts_code") or r.get("symbol") or "").upper() for r in accepted}
                for row in licensed_watch_rows:
                    symbol = str(row.get("ts_code") or row.get("symbol") or "").upper()
                    if symbol in quotes and symbol not in accepted_symbols:
                        raw = quotes[symbol].get("raw") if isinstance(quotes[symbol].get("raw"), dict) else {}
                        quotes[symbol]["raw"] = {**raw, "longhu_watch_quote_unfresh": row}
                licensed_watch_status.update({
                    "priority": "primary_when_fresh", "eligible_symbols": len(accepted),
                    "rejected_symbols": rejected,
                    "fallback_symbols": sorted(set(symbols) - {str(r.get("ts_code") or r.get("symbol")) for r in accepted}),
                })
    # Deliberately after the price merges: when the all-A snapshot fails
    # outright these merges are what put the watch basket into ``quotes`` at
    # all, and without them the volume fallback would have nothing to attach to.
    derived_flow_status = await _apply_derived_flow_metrics(
        quotes, reference_task, observed_at, dependencies,
    )
    for quote in quotes.values():
        quote["price_freshness"] = dependencies.quote_freshness(
            quote, evaluated_at, quote_timestamp_slo_seconds,
        )
    return WatchQuoteCapture(
        quotes=quotes, all_a_rows=all_a_rows, all_a_snapshot_status=all_a_snapshot_status,
        fresh_watch_rows=fresh_watch_rows, sina_watch_rows=sina_watch_rows,
        eastmoney_watch_flow_rows=eastmoney_watch_flow_rows,
        eastmoney_watch_flow_status=eastmoney_watch_flow_status,
        derived_flow_status=derived_flow_status,
        latency_ms=round((dependencies.now() - started_at) * 1000),
        licensed_watch_rows=licensed_watch_rows,
        licensed_watch_status=licensed_watch_status,
    )


__all__ = [
    "WatchQuoteCapture", "WatchQuoteCaptureDependencies", "capture_watch_quotes", "eastmoney_flow_freshness",
]
