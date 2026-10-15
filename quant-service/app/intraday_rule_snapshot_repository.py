"""Bounded persistence seam for frozen intraday rule inputs."""

from __future__ import annotations

from datetime import datetime
from typing import Any
import uuid

from psycopg.types.json import Json

from .intraday_rule_inputs import intraday_rule_input_hash, intraday_rule_input_payload


_RULE_INPUT_SNAPSHOT_SQL = """INSERT INTO quant.intraday_rule_input_snapshots(
       scan_id,symbol,observed_at,model_version,input_hash,inputs
   ) VALUES(%s,%s,%s,%s,%s,%s)
   ON CONFLICT(scan_id,symbol,model_version) DO NOTHING"""


def _snapshot_params(*, scan_id: uuid.UUID, observed_at: datetime, watch: dict[str, Any],
                     quote: dict[str, Any] | None, previous_quote: dict[str, Any] | None,
                     daily_factors: dict[str, Any] | None, minute_features: dict[str, Any] | None,
                     peer_context: dict[str, Any] | None, model_version: str,
                     market_context: dict[str, Any] | None, fast_confirmation: dict[str, Any] | None,
                     portfolio_context: dict[str, Any] | None) -> tuple[str, tuple[Any, ...]]:
    payload = intraday_rule_input_payload(
        watch=watch, quote=quote, previous_quote=previous_quote, daily_factors=daily_factors,
        minute_features=minute_features, peer_context=peer_context, model_version=model_version,
        market_context=market_context, fast_confirmation=fast_confirmation,
        portfolio_context=portfolio_context,
    )
    input_hash = intraday_rule_input_hash(payload)
    return input_hash, (scan_id, str(watch["symbol"]), observed_at, model_version, input_hash, Json(payload))


def persist_rule_input_snapshot(connection: Any, *, scan_id: uuid.UUID, observed_at: datetime,
                                watch: dict[str, Any], quote: dict[str, Any] | None,
                                previous_quote: dict[str, Any] | None,
                                daily_factors: dict[str, Any] | None,
                                minute_features: dict[str, Any] | None,
                                peer_context: dict[str, Any] | None,
                                model_version: str,
                                market_context: dict[str, Any] | None = None,
                                fast_confirmation: dict[str, Any] | None = None,
                                portfolio_context: dict[str, Any] | None = None) -> str:
    """Store one bounded replay input for every scanned watch, even no-signal rows."""
    input_hash, params = _snapshot_params(
        scan_id=scan_id, observed_at=observed_at, watch=watch, quote=quote,
        previous_quote=previous_quote, daily_factors=daily_factors, minute_features=minute_features,
        peer_context=peer_context, model_version=model_version, market_context=market_context,
        fast_confirmation=fast_confirmation, portfolio_context=portfolio_context,
    )
    connection.execute(_RULE_INPUT_SNAPSHOT_SQL, params)
    return input_hash


def persist_rule_input_snapshots(connection: Any, rows: list[dict[str, Any]]) -> list[str]:
    """Persist a scan's frozen rule inputs in one cursor batch.

    The row payload is still built per symbol so hashes and point-in-time
    fields remain identical to ``persist_rule_input_snapshot``.  Only the
    transport boundary changes; a connection replacement without cursors uses
    the original per-row execute fallback for replay/test adapters.
    """
    params: list[tuple[Any, ...]] = []
    hashes: list[str] = []
    for row in rows:
        input_hash, values = _snapshot_params(**row)
        hashes.append(input_hash)
        params.append(values)
    if not params:
        return hashes
    cursor_factory = getattr(connection, "cursor", None)
    if callable(cursor_factory):
        with cursor_factory() as cursor:
            cursor.executemany(_RULE_INPUT_SNAPSHOT_SQL, params)
    else:
        for values in params:
            connection.execute(_RULE_INPUT_SNAPSHOT_SQL, values)
    return hashes


def prune_rule_input_evidence(connection: Any, *, cutoff: datetime) -> None:
    """Bound future replay evidence without touching alerts, outcomes or daily data."""
    connection.execute(
        "DELETE FROM quant.intraday_rule_input_snapshots WHERE observed_at<%s",
        (cutoff,),
    )
    # The associated watch-price evidence is the only non-raw quote source
    # required to audit the frozen input snapshots.  Fast/order-book streams
    # have their stricter independent retention because they are attribution
    # evidence rather than core-rule inputs.
    connection.execute(
        "DELETE FROM quant.intraday_quote_observations WHERE source_name IN ('tencent_free','sina_free') AND observed_at<%s",
        (cutoff,),
    )


__all__ = ["persist_rule_input_snapshot", "persist_rule_input_snapshots", "prune_rule_input_evidence"]
