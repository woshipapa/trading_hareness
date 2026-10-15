"""Bounded orchestration for persisting intraday scan evidence.

The caller owns the database transaction(s).  This module deliberately has no
provider, clock, or alert-delivery ownership: it freezes the inputs already
gathered by the scan, evaluates candidates without a held connection when the
live split path is enabled, and delegates durable event state to injected
collaborators.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
import uuid

from .stable_json import tolerant_json

logger = logging.getLogger(__name__)


_SCAN_REJECTION_SQL = """INSERT INTO quant.intraday_scan_rejections(
       scan_id,symbol,model_version,observed_at,outcome,reason_codes,evidence)
   VALUES(%s,%s,%s,%s,%s,%s,%s)
   ON CONFLICT(scan_id,symbol,model_version) DO UPDATE SET
     outcome=EXCLUDED.outcome,reason_codes=EXCLUDED.reason_codes,evidence=EXCLUDED.evidence"""

_QUOTE_OBSERVATION_SQL = """INSERT INTO quant.intraday_quote_observations(
       scan_id,symbol,observed_at,source_name,price,pct_change,volume_ratio,turnover_rate,main_net_inflow,raw)
   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""


def _executemany_or_execute(connection: Any, statement: str, params: list[tuple[Any, ...]]) -> None:
    """Use one cursor batch while retaining the small replay connection seam."""
    if not params:
        return
    cursor_factory = getattr(connection, "cursor", None)
    if callable(cursor_factory):
        with cursor_factory() as cursor:
            cursor.executemany(statement, params)
        return
    for values in params:
        connection.execute(statement, values)


def scan_rejection_reasons(
    quote: dict[str, Any] | None,
    daily_factors: dict[str, Any] | None,
    minute_features: dict[str, Any] | None,
    generated_signals: list[dict[str, Any]],
) -> list[str]:
    """Explain why one scanned symbol did not become a research candidate.

    Rules remain pure and unchanged; this compact reason projection records
    the observable gates that were absent at scan time.  It is deliberately
    conservative: an unknown value is reported as missing rather than inferred
    to have passed.
    """
    reasons: list[str] = []
    if not quote or quote.get("price") is None:
        reasons.append("quote_missing")
    else:
        availability = quote.get("data_availability") if isinstance(quote.get("data_availability"), dict) else {}
        for field in availability.get("missing_public_flow_fields") or []:
            reasons.append(f"flow_{str(field)}_missing_or_research_only")
    if not isinstance(daily_factors, dict) or daily_factors.get("status") in {"insufficient_history", "not_available"}:
        reasons.append("daily_history_insufficient")
    minute = minute_features if isinstance(minute_features, dict) else {}
    if minute.get("status") in {None, "not_available", "unavailable", "insufficient_history"}:
        reasons.append("minute_confirmation_missing")
    if not generated_signals:
        reasons.append("no_rule_condition_met")
    else:
        for signal in generated_signals:
            for flag in signal.get("risk_flags") or []:
                if str(flag) not in reasons:
                    reasons.append(str(flag))
    return list(dict.fromkeys(reasons)) or ["no_rule_condition_met"]


@dataclass(frozen=True)
class IntradayScanSignalPersistenceDependencies:
    prepare_inputs: Callable[..., Any]
    preparation_dependencies: Any
    quote_source: Callable[[dict[str, Any] | None], str]
    json_safe: Callable[[Any], Any]
    persist_rule_input_snapshot: Callable[..., Any]
    attach_volume_time_profile: Callable[..., dict[str, Any] | None]
    number: Callable[[Any], float | None]
    aggregate_order_book_observations: Callable[..., dict[str, Any] | None]
    generate_signals: Callable[..., list[dict[str, Any]]]
    signal_generation_dependencies: Any
    load_event_state: Callable[..., Any]
    persist_generated_signals: Callable[..., list[dict[str, Any]]]
    signal_event_persistence_dependencies: Any
    persist_rule_input_snapshots: Callable[..., Any] | None = None
    # Production scans use the basket form below. Keep the single-symbol
    # callback as a compatibility fallback for replay and small test adapters.
    load_event_states: Callable[..., dict[str, Any]] | None = None
    # Heavy per-stock evidence (raw quote row, rule-input snapshot) at most every
    # N seconds unless the stock produced a signal; ``None`` keeps every scan.
    evidence_throttle: Any = None
    # Compact per-scan tape of every watched stock (``watch_scan_tape``).
    tape_record: Callable[..., dict[str, Any]] | None = None
    persist_scan_tape: Callable[..., Any] | None = None


@dataclass(frozen=True)
class IntradayScanPersistenceServiceDependencies:
    """Application-owned collaborators for the scan persistence boundary.

    ``persist_scan_signals`` deliberately receives an already-open connection
    so it remains straightforward to unit-test.  The live service may opt
    into the split path below: the scan receipt and frozen point-in-time
    inputs commit first, pure rule evaluation runs without a held connection,
    and event de-duplication plus signal writes commit in a second transaction.
    The receipt is marked ``partial`` between the two phases and ``failed`` on
    a recovery write if the second phase cannot complete.
    """

    database: Any
    signal_dependencies: IntradayScanSignalPersistenceDependencies
    confirmation_window: Any
    signal_model_version: str
    factor_contract_version: str
    # Production enables this after composing the full batch-evidence graph.
    # Small replay/test adapters retain the original one-transaction contract.
    split_transaction: bool = False


@dataclass
class _IntradayScanEvaluation:
    """Frozen strategy inputs and pure outputs carried between scan phases."""

    prepared: Any
    prepared_rows: list[dict[str, Any]]
    signal_keys_by_symbol: dict[str, list[str]]
    rejection_params: list[tuple[Any, ...]]
    heavy_quote_params: list[tuple[Any, ...]]
    heavy_snapshot_rows: list[dict[str, Any]]


def persist_scan_transaction(
    dependencies: IntradayScanPersistenceServiceDependencies,
    *,
    scan_id: uuid.UUID,
    observed_at: datetime,
    selected_symbols: list[str],
    source_status: dict[str, Any],
    watches: list[dict[str, Any]],
    quotes: dict[str, dict[str, Any]],
    all_a_rows: list[dict[str, Any]],
    quote_latency_ms: int,
    realtime_minutes: dict[str, dict[str, Any]],
    surge_features: dict[str, dict[str, Any]],
    peer_contexts: dict[str, dict[str, Any]],
    fast_confirmations: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Persist a live scan with an optional connection-free evaluation gap.

    The compatibility path keeps the historical one-transaction contract.
    Production opts into two phases: the first transaction commits the scan
    receipt and frozen local inputs, pure rule evaluation runs while no
    database connection is held, and the second transaction reads event state
    and writes signals/evidence.  A partial receipt is made explicit between
    phases and converted to ``failed`` if the second phase cannot finish.
    """
    common = {
        "scan_id": scan_id, "observed_at": observed_at, "selected_symbols": selected_symbols,
        "source_status": source_status, "watches": watches, "quotes": quotes,
        "all_a_rows": all_a_rows, "quote_latency_ms": quote_latency_ms,
        "realtime_minutes": realtime_minutes, "surge_features": surge_features,
        "peer_contexts": peer_contexts, "fast_confirmations": fast_confirmations,
        "confirmation_window": dependencies.confirmation_window,
        "signal_model_version": dependencies.signal_model_version,
        "factor_contract_version": dependencies.factor_contract_version,
        "dependencies": dependencies.signal_dependencies,
    }
    # The pure evaluation phase cannot run without a connection unless the
    # complete batch evidence writer is present.  Treat an incomplete adapter
    # graph as a compatibility adapter so a config mistake cannot turn every
    # scan into a ``None.execute`` failure.
    split_transaction = (
        dependencies.split_transaction
        and dependencies.signal_dependencies.persist_rule_input_snapshots is not None
    )
    if not split_transaction:
        with dependencies.database.transaction() as connection:
            return persist_scan_signals(connection, **common)

    prepared_committed = False
    try:
        with dependencies.database.transaction() as connection:
            prepared = dependencies.signal_dependencies.prepare_inputs(
                connection,
                scan_id=scan_id,
                observed_at=observed_at,
                selected_symbols=selected_symbols,
                source_status=source_status,
                watches=watches,
                quotes=quotes,
                all_a_rows=all_a_rows,
                quote_latency_ms=quote_latency_ms,
                realtime_minutes=realtime_minutes,
                surge_features=surge_features,
                confirmation_window=dependencies.confirmation_window,
                dependencies=dependencies.signal_dependencies.preparation_dependencies,
            )
            # The receipt is visible to the second phase and to recovery/read
            # models, but cannot be mistaken for a completed signal scan.
            connection.execute(
                "UPDATE quant.intraday_scan_runs SET status='partial' WHERE scan_id=%s",
                (scan_id,),
            )
        prepared_committed = True

        # No connection is held across this call.  In particular, the pure
        # rule functions and tape-independent feature projections can use the
        # process CPU without occupying a PostgreSQL/tunnel slot.
        evaluation = _build_scan_evaluation(None, **common, prepared=prepared)
        with dependencies.database.transaction() as connection:
            signals = _commit_scan_evaluation(connection, **common, evaluation=evaluation)
            connection.execute(
                "UPDATE quant.intraday_scan_runs SET status='completed' WHERE scan_id=%s",
                (scan_id,),
            )
        return signals
    except BaseException:
        if prepared_committed:
            try:
                with dependencies.database.transaction() as connection:
                    connection.execute(
                        """UPDATE quant.intraday_scan_runs
                              SET status='failed', summary=summary || %s::jsonb
                            WHERE scan_id=%s""",
                        (tolerant_json({"signal_persistence": {"status": "failed"}}), scan_id),
                    )
            except Exception:  # noqa: BLE001 - retain the original scan error
                logger.warning("could not mark scan %s failed after split persistence error", scan_id, exc_info=True)
        raise


def persist_scan_signals(
    connection: Any,
    *,
    scan_id: uuid.UUID,
    observed_at: datetime,
    selected_symbols: list[str],
    source_status: dict[str, Any],
    watches: list[dict[str, Any]],
    quotes: dict[str, dict[str, Any]],
    all_a_rows: list[dict[str, Any]],
    quote_latency_ms: int,
    realtime_minutes: dict[str, dict[str, Any]],
    surge_features: dict[str, dict[str, Any]],
    peer_contexts: dict[str, dict[str, Any]],
    fast_confirmations: dict[str, dict[str, Any]],
    confirmation_window: Any,
    signal_model_version: str,
    factor_contract_version: str,
    dependencies: IntradayScanSignalPersistenceDependencies,
) -> list[dict[str, Any]]:
    """Persist one scan using the caller's already-open transaction.

    This is the stable compatibility seam for replay and lightweight adapters.
    The live two-phase adapter calls the same prepare/commit helpers from two
    separate transactions.
    """
    evaluation = _build_scan_evaluation(
        connection, scan_id=scan_id, observed_at=observed_at, selected_symbols=selected_symbols,
        source_status=source_status, watches=watches, quotes=quotes, all_a_rows=all_a_rows,
        quote_latency_ms=quote_latency_ms, realtime_minutes=realtime_minutes, surge_features=surge_features,
        peer_contexts=peer_contexts, fast_confirmations=fast_confirmations,
        confirmation_window=confirmation_window, signal_model_version=signal_model_version,
        factor_contract_version=factor_contract_version, dependencies=dependencies,
    )
    return _commit_scan_evaluation(
        connection, scan_id=scan_id, observed_at=observed_at, selected_symbols=selected_symbols,
        source_status=source_status, watches=watches, quotes=quotes, all_a_rows=all_a_rows,
        quote_latency_ms=quote_latency_ms, realtime_minutes=realtime_minutes, surge_features=surge_features,
        peer_contexts=peer_contexts, fast_confirmations=fast_confirmations,
        confirmation_window=confirmation_window, signal_model_version=signal_model_version,
        factor_contract_version=factor_contract_version, dependencies=dependencies, evaluation=evaluation,
    )


def _build_scan_evaluation(
    connection: Any,
    *,
    scan_id: uuid.UUID,
    observed_at: datetime,
    selected_symbols: list[str],
    source_status: dict[str, Any],
    watches: list[dict[str, Any]],
    quotes: dict[str, dict[str, Any]],
    all_a_rows: list[dict[str, Any]],
    quote_latency_ms: int,
    realtime_minutes: dict[str, dict[str, Any]],
    surge_features: dict[str, dict[str, Any]],
    peer_contexts: dict[str, dict[str, Any]],
    fast_confirmations: dict[str, dict[str, Any]],
    confirmation_window: Any,
    signal_model_version: str,
    factor_contract_version: str,
    dependencies: IntradayScanSignalPersistenceDependencies,
    prepared: Any | None = None,
) -> _IntradayScanEvaluation:
    """Freeze local state and evaluate rules without reading event state."""
    if prepared is None:
        prepared = dependencies.prepare_inputs(
            connection, scan_id=scan_id, observed_at=observed_at, selected_symbols=selected_symbols,
            source_status=source_status, watches=watches, quotes=quotes, all_a_rows=all_a_rows,
            quote_latency_ms=quote_latency_ms, realtime_minutes=realtime_minutes, surge_features=surge_features,
            confirmation_window=confirmation_window, dependencies=dependencies.preparation_dependencies,
        )
    # Freeze and evaluate the complete watch basket before reading event state.
    # Rule generation is pure; the production repository can therefore fetch
    # the three event-state projections once instead of once per symbol.
    prepared_rows: list[dict[str, Any]] = []
    signal_keys_by_symbol: dict[str, list[str]] = {}
    rejection_params: list[tuple[Any, ...]] = []
    heavy_quote_params: list[tuple[Any, ...]] = []
    heavy_snapshot_rows: list[dict[str, Any]] = []
    for watch in watches:
        symbol = str(watch["symbol"])
        quote = quotes.get(symbol)
        quote_source_name = dependencies.quote_source(quote)
        previous = prepared.previous_by_symbol.get(symbol)
        daily_factors = prepared.daily_factors_by_symbol.get(symbol, {"status": "insufficient_history", "bar_count": 0})
        minute_feature = dependencies.attach_volume_time_profile(
            prepared.raw_minute_features_by_symbol.get(symbol), prepared.minute_volume_profiles_by_symbol.get(symbol),
            number=dependencies.number,
        )
        order_book_feature = dependencies.aggregate_order_book_observations(
            prepared.order_book_by_symbol.get(symbol, []), observed_at,
        )
        peer_context = peer_contexts.get(symbol)
        previous_quote = dict(previous) if previous else None
        fast_confirmation = fast_confirmations.get(symbol, {"status": "missing", "max_age_seconds": 30})
        market_context = prepared.market_contexts.get((observed_at, symbol), {})
        portfolio_context = {
            "position": prepared.paper_positions.get(symbol) or {},
            "snapshot": prepared.snapshot_payload,
            "candidate_sector_keys": prepared.candidate_sector_keys.get(symbol, ()),
        }

        def persist_heavy_evidence() -> None:
            if dependencies.persist_rule_input_snapshots is not None:
                if quote:
                    quote_raw = dict(quote.get("raw") or {})
                    quote_raw["_observation_source"] = quote_source_name
                    quote_raw["_price_source"] = quote.get("price_source")
                    heavy_quote_params.append((
                        scan_id, symbol, observed_at, quote_source_name, quote.get("price"), quote.get("pct_change"),
                        quote.get("volume_ratio"), quote.get("turnover_rate"), quote.get("main_net_inflow"),
                        tolerant_json(dependencies.json_safe(quote_raw)),
                    ))
                heavy_snapshot_rows.append({
                    "scan_id": scan_id, "observed_at": observed_at, "watch": watch, "quote": quote,
                    "previous_quote": previous_quote, "daily_factors": daily_factors,
                    "minute_features": minute_feature, "peer_context": peer_context,
                    "model_version": signal_model_version, "market_context": market_context,
                    "fast_confirmation": fast_confirmation, "portfolio_context": portfolio_context,
                })
                return
            if quote:
                quote_raw = dict(quote.get("raw") or {})
                quote_raw["_observation_source"] = quote_source_name
                quote_raw["_price_source"] = quote.get("price_source")
                connection.execute(
                    _QUOTE_OBSERVATION_SQL,
                    (
                        scan_id, symbol, observed_at, quote_source_name, quote.get("price"), quote.get("pct_change"),
                        quote.get("volume_ratio"), quote.get("turnover_rate"), quote.get("main_net_inflow"),
                        tolerant_json(dependencies.json_safe(quote_raw)),
                    ),
                )
            dependencies.persist_rule_input_snapshot(
                connection, scan_id=scan_id, observed_at=observed_at, watch=watch, quote=quote,
                previous_quote=previous_quote, daily_factors=daily_factors, minute_features=minute_feature,
                peer_context=peer_context, model_version=signal_model_version,
                market_context=market_context, fast_confirmation=fast_confirmation,
                portfolio_context=portfolio_context,
            )

        # Freeze the inputs before generating. Heavy evidence is sampled per
        # stock; a stock that produces a signal outside its sample still gets
        # its unchanged inputs stored.
        sampled = dependencies.evidence_throttle is None or dependencies.evidence_throttle.due(symbol, observed_at)
        if sampled:
            persist_heavy_evidence()
        generated_signals = dependencies.generate_signals(
            watch=watch, symbol=symbol, quote=quote, previous_quote=previous_quote,
            daily_factors=daily_factors, minute_features=minute_feature, peer_context=peer_context,
            shadow_prior=prepared.shadow_priors.get(symbol), rebound_prior=prepared.rebound_priors.get(symbol),
            first_eac=prepared.first_eac_by_symbol.get(symbol), observed_at=observed_at,
            dependencies=dependencies.signal_generation_dependencies,
        )
        if generated_signals and not sampled:
            dependencies.evidence_throttle.due(symbol, observed_at, force=True)
            persist_heavy_evidence()
        prepared_rows.append({
            "symbol": symbol, "watch": watch, "quote": quote, "daily_factors": daily_factors,
            "minute_feature": minute_feature, "peer_context": peer_context,
            "market_context": market_context, "fast_confirmation": fast_confirmation,
            "order_book_feature": order_book_feature, "previous_quote": previous_quote,
            "generated_signals": generated_signals,
        })
        if generated_signals:
            signal_keys_by_symbol[symbol] = [str(signal["signal_key"]) for signal in generated_signals]

    return _IntradayScanEvaluation(
        prepared=prepared, prepared_rows=prepared_rows,
        signal_keys_by_symbol=signal_keys_by_symbol, rejection_params=rejection_params,
        heavy_quote_params=heavy_quote_params, heavy_snapshot_rows=heavy_snapshot_rows,
    )


def _commit_scan_evaluation(
    connection: Any,
    *,
    scan_id: uuid.UUID,
    observed_at: datetime,
    selected_symbols: list[str],
    source_status: dict[str, Any],
    watches: list[dict[str, Any]],
    quotes: dict[str, dict[str, Any]],
    all_a_rows: list[dict[str, Any]],
    quote_latency_ms: int,
    realtime_minutes: dict[str, dict[str, Any]],
    surge_features: dict[str, dict[str, Any]],
    peer_contexts: dict[str, dict[str, Any]],
    fast_confirmations: dict[str, dict[str, Any]],
    confirmation_window: Any,
    signal_model_version: str,
    factor_contract_version: str,
    dependencies: IntradayScanSignalPersistenceDependencies,
    evaluation: _IntradayScanEvaluation,
) -> list[dict[str, Any]]:
    """Read event state and commit evidence/signals for a frozen evaluation."""
    prepared = evaluation.prepared
    prepared_rows = evaluation.prepared_rows
    signal_keys_by_symbol = evaluation.signal_keys_by_symbol
    rejection_params = evaluation.rejection_params
    if dependencies.persist_rule_input_snapshots is not None:
        _executemany_or_execute(connection, _QUOTE_OBSERVATION_SQL, evaluation.heavy_quote_params)
        dependencies.persist_rule_input_snapshots(connection, evaluation.heavy_snapshot_rows)

    batch_event_states: dict[str, Any] = {}
    if dependencies.load_event_states is not None and signal_keys_by_symbol:
        batch_event_states = dependencies.load_event_states(
            connection, signal_keys_by_symbol, session_start=prepared.session_start,
        )

    signals: list[dict[str, Any]] = []
    tape_rows: dict[str, dict[str, Any]] = {}
    for row in prepared_rows:
        symbol = row["symbol"]
        watch = row["watch"]
        quote = row["quote"]
        daily_factors = row["daily_factors"]
        minute_feature = row["minute_feature"]
        peer_context = row["peer_context"]
        market_context = row["market_context"]
        fast_confirmation = row["fast_confirmation"]
        order_book_feature = row["order_book_feature"]
        generated_signals = row["generated_signals"]
        event_state = batch_event_states.get(symbol) if dependencies.load_event_states is not None else None
        if event_state is None:
            event_state = dependencies.load_event_state(
                connection, [str(signal["signal_key"]) for signal in generated_signals], symbol,
                session_start=prepared.session_start,
            )
        persisted = dependencies.persist_generated_signals(
            connection, scan_id=scan_id, observed_at=observed_at, symbol=symbol, watch=watch,
            quote=quote, daily_factors=daily_factors, minute_feature=minute_feature,
            peer_context=peer_context, market_context=market_context, fast_confirmation=fast_confirmation,
            order_book_feature=order_book_feature, realtime_minute=realtime_minutes.get(symbol),
            paper_position=prepared.paper_positions.get(symbol), portfolio_snapshot=prepared.snapshot_payload,
            candidate_sector_keys=prepared.candidate_sector_keys.get(symbol, ()), probability_profiles=prepared.probability_profiles,
            generated_signals=generated_signals, existing_event_state=event_state,
            confirmation_window=confirmation_window, factor_contract_version=factor_contract_version,
            dependencies=dependencies.signal_event_persistence_dependencies,
        )
        signals.extend(persisted)
        if dependencies.tape_record is not None:
            try:
                tape_rows[symbol] = dependencies.tape_record(symbol, quote, minute_feature, peer_context, persisted, observed_at)
            except Exception:  # noqa: BLE001 - the tape is research evidence; the scan proceeds
                pass
        outcome = "candidate" if persisted else "rejected"
        if persisted and all(str(item.get("state") or "") == "suppressed" for item in persisted):
            outcome = "suppressed"
        rejection_params.append((
            scan_id, symbol, signal_model_version, observed_at, outcome,
            tolerant_json(scan_rejection_reasons(quote, daily_factors, minute_feature, generated_signals)),
            tolerant_json({"generated_signal_count": len(generated_signals), "persisted_signal_count": len(persisted),
                           "quote_available": bool(quote), "daily_status": daily_factors.get("status"),
                           "minute_status": minute_feature.get("status") if isinstance(minute_feature, dict) else None}),
        ))
    if rejection_params:
        cursor_factory = getattr(connection, "cursor", None)
        if callable(cursor_factory):
            # psycopg sends the whole rejection projection through one cursor
            # round trip; lightweight test/replay connections without cursors
            # retain the old execute fallback below.
            with cursor_factory() as cursor:
                cursor.executemany(_SCAN_REJECTION_SQL, rejection_params)
        else:
            for params in rejection_params:
                connection.execute(_SCAN_REJECTION_SQL, params)
    if tape_rows and dependencies.persist_scan_tape is not None:
        # Research evidence in a savepoint: a failed tape insert must never
        # roll back the scan's signals.
        try:
            with connection.transaction() if hasattr(connection, "transaction") else contextlib.nullcontext():
                dependencies.persist_scan_tape(connection, scan_id=scan_id, observed_at=observed_at, rows=tape_rows)
        except Exception:  # noqa: BLE001
            logger.warning("watch scan tape not stored for scan %s", scan_id, exc_info=True)
    return signals


__all__ = [
    "IntradayScanPersistenceServiceDependencies",
    "IntradayScanSignalPersistenceDependencies",
    "persist_scan_signals",
    "persist_scan_transaction",
    "scan_rejection_reasons",
]
