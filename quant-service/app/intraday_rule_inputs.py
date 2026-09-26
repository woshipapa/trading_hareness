"""Immutable, minimal inputs for replaying intraday rules and policy gates.

Live scans already persist quotes and emitted signals.  They did not preserve
the complete *non-signal* input set, which makes a later replay unable to
distinguish "the rule did not fire" from "the evidence was never recorded".
The v2 contract captures the pure ``signal_rules`` inputs *and* the bounded
same-scan inputs used by the policy and paper-risk gates.  It intentionally
excludes provider raw payloads and does not create a trading instruction.

v3 adds the session OHLC the teacher rules read.  v2 dropped ``raw`` entirely,
but ``teacher_review_rules.scan_features`` takes open/high/low/amount/volume and
the order book from ``raw.longhu_watch_quote`` - so every replay reported them as
missing inputs while the live scan had them.  On 2026-09-24 that made 我爱我家 and
天顺风能 read "输入缺失：开盘价（全天 100% 的扫描无法判定）" although their live cards
had passed 竞价不低开; 2026-09-23 三羊马 and 博通集成 were the same defect.  What is
kept is a fixed, named list of scalars plus a two-field order-book summary -
still never an upstream body.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any


INTRADAY_RULE_INPUT_SCHEMA_VERSION = "intraday-rule-input-v3"
LEGACY_INTRADAY_RULE_INPUT_SCHEMA_VERSION = "intraday-rule-input-v1"
#: Superseded but still replayable; v2 rows simply lack the session OHLC.
PRIOR_INTRADAY_RULE_INPUT_SCHEMA_VERSION = "intraday-rule-input-v2"

#: The only raw-quote values a replay may keep: what the teacher rules read.
_RAW_QUOTE_FIELDS = ("pre_close", "open", "high", "low", "amount", "volume", "turnover_rate", "volume_ratio")
#: Raw sub-objects ``scan_features`` looks into, in its own preference order.
_RAW_QUOTE_SOURCES = ("longhu_watch_quote", "watch_quote", "all_a_snapshot_row")
_ORDER_BOOK_FIELDS = ("book_side", "seal_volume_lot")

_WATCH_FIELDS = (
    "symbol", "alert_on_entry", "alert_on_exit", "entry_price", "available_quantity",
    "hard_stop", "take_profit", "metadata",
)
_QUOTE_FIELDS = (
    "symbol", "price", "pct_change", "volume_ratio", "turnover_rate", "main_net_inflow",
    "main_flow_percentile", "price_source", "price_freshness",
    # The live rule decides per field whether a flow value may be used from
    # these labels; without them a replay silently used values the live scan
    # had zeroed.  Older snapshots lack the key and replay as before.
    "flow_metric_sources",
)
_PREVIOUS_QUOTE_FIELDS = ("symbol", "source_name", "price", "pct_change", "observed_at")
_MARKET_CONTEXT_FIELDS = ("status", "market_state", "board_snapshot_age_seconds")
_FAST_CONFIRMATION_FIELDS = ("status", "max_age_seconds", "price_difference_pct")
_PORTFOLIO_SNAPSHOT_FIELDS = ("drawdown", "daily_return", "gross_exposure", "sector_exposure")
_PORTFOLIO_POSITION_FIELDS = ("symbol", "target_weight", "quantity", "sellable_quantity", "average_cost")


def _quote_for_policy(quote: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep policy-relevant provenance plus the session OHLC, never a raw body."""
    selected = _selected(quote, _QUOTE_FIELDS)
    if selected is None:
        return None
    raw = (quote or {}).get("raw")
    if isinstance(raw, dict):
        bounded: dict[str, Any] = {}
        for source in _RAW_QUOTE_SOURCES:
            body = raw.get(source)
            if not isinstance(body, dict):
                continue
            kept = {key: _json_value(body.get(key)) for key in _RAW_QUOTE_FIELDS if body.get(key) is not None}
            book = body.get("order_book")
            if isinstance(book, dict):
                kept["order_book"] = {key: _json_value(book.get(key)) for key in _ORDER_BOOK_FIELDS
                                      if book.get(key) is not None}
            if kept:
                bounded[source] = kept
        if bounded:
            selected["raw"] = bounded
    flow_snapshot = (quote or {}).get("flow_snapshot")
    if isinstance(flow_snapshot, dict):
        selected["flow_snapshot"] = {
            "decision_eligible": _json_value(flow_snapshot.get("decision_eligible")),
            "scope": _json_value(flow_snapshot.get("scope")),
            "cross_sectional": _json_value(flow_snapshot.get("cross_sectional")),
            "source": _json_value(flow_snapshot.get("source")),
        }
    return selected


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _selected(value: dict[str, Any] | None, fields: tuple[str, ...]) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {field: _json_value(value.get(field)) for field in fields if field in value}


def intraday_rule_input_payload(*, watch: dict[str, Any], quote: dict[str, Any] | None,
                                previous_quote: dict[str, Any] | None,
                                daily_factors: dict[str, Any] | None,
                                minute_features: dict[str, Any] | None,
                                peer_context: dict[str, Any] | None,
                                model_version: str,
                                market_context: dict[str, Any] | None = None,
                                fast_confirmation: dict[str, Any] | None = None,
                                portfolio_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Freeze causal inputs for pure rules and their pre-confirmation gate."""
    portfolio_context = portfolio_context if isinstance(portfolio_context, dict) else {}
    portfolio_position = portfolio_context.get("position")
    portfolio_snapshot = portfolio_context.get("snapshot")
    return {
        "schema_version": INTRADAY_RULE_INPUT_SCHEMA_VERSION,
        "model_version": str(model_version),
        "watch": _selected(watch, _WATCH_FIELDS) or {},
        "quote": _quote_for_policy(quote),
        "previous_quote": _selected(previous_quote, _PREVIOUS_QUOTE_FIELDS),
        "daily_factors": _json_value(daily_factors or {"status": "not_available"}),
        "minute_features": _json_value(minute_features or {"status": "not_available"}),
        "peer_context": _json_value(peer_context or {"status": "not_available"}),
        # These values are intentionally captured before rules emit.  A
        # future replay recomputes paper risk per emitted signal type from the
        # same frozen portfolio state, rather than reading today's ledger.
        "market_context": _selected(market_context, _MARKET_CONTEXT_FIELDS) or {},
        "fast_confirmation": _selected(fast_confirmation, _FAST_CONFIRMATION_FIELDS) or {},
        "portfolio_context": {
            "position": _selected(portfolio_position, _PORTFOLIO_POSITION_FIELDS) or {},
            "snapshot": _selected(portfolio_snapshot, _PORTFOLIO_SNAPSHOT_FIELDS) or {},
            "candidate_sector_keys": _json_value(list(portfolio_context.get("candidate_sector_keys") or ())),
        },
    }


def intraday_rule_input_hash(payload: dict[str, Any]) -> str:
    """Hash canonical JSON so a replay input set is immutable and auditable."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def intraday_rule_replay_inputs(payload: dict[str, Any], *, expected_model_version: str | None = None) -> dict[str, Any]:
    """Validate a stored contract before a caller reruns the pure rule function."""
    schema_version = str(payload.get("schema_version") or "")
    if schema_version not in {LEGACY_INTRADAY_RULE_INPUT_SCHEMA_VERSION,
                              PRIOR_INTRADAY_RULE_INPUT_SCHEMA_VERSION,
                              INTRADAY_RULE_INPUT_SCHEMA_VERSION}:
        raise ValueError("unsupported intraday rule input schema")
    model_version = str(payload.get("model_version") or "")
    if expected_model_version and model_version != expected_model_version:
        raise ValueError("intraday rule input model version does not match replay model")
    watch = payload.get("watch")
    if not isinstance(watch, dict) or not watch.get("symbol"):
        raise ValueError("intraday rule input requires a watch symbol")
    for key in ("daily_factors", "minute_features", "peer_context"):
        if not isinstance(payload.get(key), dict):
            raise ValueError(f"intraday rule input requires object {key}")
    result = {
        "watch": dict(watch), "quote": dict(payload["quote"]) if isinstance(payload.get("quote"), dict) else None,
        "previous_quote": (dict(payload["previous_quote"])
                           if isinstance(payload.get("previous_quote"), dict) else None),
        "daily_factors": dict(payload["daily_factors"]), "minute_features": dict(payload["minute_features"]),
        "peer_context": dict(payload["peer_context"]), "model_version": model_version,
        "schema_version": schema_version,
        # v2 rows carry the same policy block; they only lack the session OHLC.
        "policy_replayable": schema_version in {PRIOR_INTRADAY_RULE_INPUT_SCHEMA_VERSION,
                                                INTRADAY_RULE_INPUT_SCHEMA_VERSION},
        "ohlc_replayable": schema_version == INTRADAY_RULE_INPUT_SCHEMA_VERSION,
    }
    if schema_version in {PRIOR_INTRADAY_RULE_INPUT_SCHEMA_VERSION, INTRADAY_RULE_INPUT_SCHEMA_VERSION}:
        for key in ("market_context", "fast_confirmation", "portfolio_context"):
            if not isinstance(payload.get(key), dict):
                raise ValueError(f"intraday policy replay input requires object {key}")
        portfolio = dict(payload["portfolio_context"])
        position, snapshot = portfolio.get("position"), portfolio.get("snapshot")
        if not isinstance(position, dict) or not isinstance(snapshot, dict) or not isinstance(portfolio.get("candidate_sector_keys"), list):
            raise ValueError("intraday policy replay input has an invalid portfolio_context")
        result["market_context"] = dict(payload["market_context"])
        result["fast_confirmation"] = dict(payload["fast_confirmation"])
        result["portfolio_context"] = {
            "position": dict(position), "snapshot": dict(snapshot),
            "candidate_sector_keys": list(portfolio["candidate_sector_keys"]),
        }
    return result


__all__ = [
    "INTRADAY_RULE_INPUT_SCHEMA_VERSION", "LEGACY_INTRADAY_RULE_INPUT_SCHEMA_VERSION",
    "PRIOR_INTRADAY_RULE_INPUT_SCHEMA_VERSION",
    "intraday_rule_input_hash", "intraday_rule_input_payload",
    "intraday_rule_replay_inputs",
]
