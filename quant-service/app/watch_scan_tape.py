"""Compact per-scan tape of every watched stock, and bounded heavy evidence.

The review workflow (a stock's day, its minute-level changes, its sector
relationships, recurring patterns) needs every scan of every watched stock,
but the full replay evidence - the raw quote row and the rule-input snapshot,
whose peer/sector context alone is ~29 KB - would cost ~650 MB a day at the
5 s morning cadence.  So:

* every scan writes one compact tape row holding each watched stock's
  measurements (price/OHLC/cumulative volume and amount/VWAP, volume ratio,
  turnover, seal, minute indicators, sector breadth, the scan's signals);
* the heavy evidence is written per stock when it produced a signal in that
  scan, and otherwise at most every ``min_seconds``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from psycopg.types.json import Json

_CN_TZ = ZoneInfo("Asia/Shanghai")
TAPE_PROVIDER = "quant_scan"
TAPE_CAPABILITY = "watch_scan_tape"
TAPE_VERSION = "watch-scan-tape-v1"


class EvidenceThrottle:
    """Heavy per-stock evidence at most every ``min_seconds`` unless forced (a signal)."""

    def __init__(self, min_seconds: float = 30.0) -> None:
        self.min_seconds = float(min_seconds)
        self._day: date | None = None
        self._last: dict[str, datetime] = {}

    def due(self, symbol: str, observed_at: datetime, *, force: bool = False) -> bool:
        day = observed_at.astimezone(_CN_TZ).date()
        if day != self._day:
            self._day, self._last = day, {}
        last = self._last.get(symbol)
        if force or last is None or (observed_at - last).total_seconds() >= self.min_seconds:
            self._last[symbol] = observed_at
            return True
        return False


def _num(value: Any) -> float | None:
    try:
        return round(float(value), 6) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def tape_record(symbol: str, quote: Mapping[str, Any] | None, minute: Mapping[str, Any] | None,
                peer_context: Mapping[str, Any] | None, signals: list[Mapping[str, Any]],
                observed_at: datetime) -> dict[str, Any]:
    """One stock's measurements for one scan, with short keys (see ``TAPE_FIELDS``)."""
    from .teacher_review_rules import scan_features

    record: dict[str, Any] = {}
    if quote and _num(quote.get("price")) is not None:
        try:
            f = scan_features(symbol, quote, minute, observed_at)
        except Exception:  # noqa: BLE001 - a malformed row keeps its bare price
            f = {"price": _num(quote.get("price")), "sources": {}}
        record.update({
            "p": f.get("price"), "pct": f.get("pct"), "o": f.get("open"), "h": f.get("high"), "l": f.get("low"),
            "pc": f.get("pre_close"), "v": f.get("volume_lot"), "a": f.get("amount"), "vw": f.get("vwap"),
            "vr": f.get("volume_ratio"), "to": f.get("turnover_pct"), "lim": f.get("limit_up_price"),
            "sealed": f.get("sealed"), "touched": f.get("touched_limit"),
            "src": quote.get("price_source"),
            "fresh": (quote.get("price_freshness") or {}).get("status") if isinstance(quote.get("price_freshness"), Mapping) else None,
        })
    minute = minute if isinstance(minute, Mapping) else {}
    if minute.get("price") is not None or minute.get("vwap") is not None:
        record["m"] = {key: _num(minute.get(name)) for key, name in (
            ("r1", "return_1m_pct"), ("r5", "return_5m_pct"), ("vm", "minute_volume_multiple"),
            ("avw", "above_vwap_pct"), ("rng", "session_range_position"), ("ro", "return_from_open_pct"))}
        record["m"]["t"] = minute.get("time")
    peer = peer_context if isinstance(peer_context, Mapping) else {}
    if peer:
        group = peer.get("selected_group")
        record["sec"] = {
            "g": (group.get("group_key") or group.get("label") or group.get("name")) if isinstance(group, Mapping) else group,
            "gb": _num(peer.get("group_breadth")), "cb": _num(peer.get("confirming_breadth")),
            "cp": peer.get("confirming_peer_count"), "ap": peer.get("available_peer_count"),
        }
    if signals:
        record["sig"] = [f"{item.get('signal_type')}:{item.get('state')}:{item.get('signal_key')}" for item in signals][:8]
    return {key: value for key, value in record.items() if value is not None}


def persist_scan_tape(connection: Any, *, scan_id: Any, observed_at: datetime,
                      rows: Mapping[str, Mapping[str, Any]]) -> bool:
    """One ``raw_market_observations`` row per scan (``symbol='watch:scan'``)."""
    if not rows:
        return False
    body = {"scan_id": str(scan_id), "observed_at": observed_at.isoformat(), "version": TAPE_VERSION,
            "rows": dict(rows), "provider_key": TAPE_PROVIDER, "capability": TAPE_CAPABILITY}
    # This write shares the scan's transaction: round-trip through JSON so no
    # value (a Decimal from a sector read, say) can make the insert fail.
    serialized = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
    body = json.loads(serialized)
    row = connection.execute(
        """INSERT INTO quant.raw_market_observations(provider_key,capability,market,symbol,effective_at,available_at,payload_sha256,normalized,payload)
           VALUES(%s,%s,'cn','watch:scan',%s,%s,%s,%s,%s)
           ON CONFLICT(provider_key,capability,market,symbol,effective_at,payload_sha256) DO NOTHING
           RETURNING observation_id""",
        (TAPE_PROVIDER, TAPE_CAPABILITY, observed_at, observed_at,
         hashlib.sha256(serialized.encode()).hexdigest(), Json(body), Json(body)),
    ).fetchone()
    return row is not None


TAPE_FIELDS = {
    "p": "price", "pct": "change %", "o/h/l/pc": "open/high/low/pre-close", "v": "cumulative volume (lots)",
    "a": "cumulative amount (yuan)", "vw": "session VWAP", "vr": "volume ratio", "to": "turnover %",
    "lim": "limit-up price", "sealed/touched": "sealed now / touched the limit", "src/fresh": "price source and timestamp status",
    "m": "minute indicators r1/r5 (returns), vm (volume multiple), avw (vs VWAP %), rng (range position), ro (from open)",
    "sec": "sector group g, breadth gb/cb, confirming/available peers cp/ap", "sig": "signals this scan (type:state:key)",
}

__all__ = ["EvidenceThrottle", "TAPE_CAPABILITY", "TAPE_FIELDS", "TAPE_PROVIDER", "persist_scan_tape", "tape_record"]
