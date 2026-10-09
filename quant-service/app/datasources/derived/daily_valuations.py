"""Dated Fuyao valuation evidence -> attributable daily valuation fields."""
from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")
VERSION = "fuyao-daily-valuation-v1"
EQUITY = re.compile(r"^(?:(?:60[0135]|68[0-9])[0-9]{3}\.SH|(?:000|001|002|003|300|301|302)[0-9]{3}\.SZ|[489][0-9]{5}\.BJ)$")
SOURCE_FIELDS = ("pe_ttm", "pe_mrq", "pb_mrq", "ps_ttm", "pcf_ttm")


def aware_time(value: Any) -> datetime | None:
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def ratio(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def projection_row(evidence: Mapping[str, Any], day: date, projected_at: datetime) -> dict[str, Any] | None:
    """Accept only this session's post-close evidence; never manufacture daily-basic fields.

    ``pe`` is explicitly trailing-twelve-month PE and ``pb`` latest-quarter PB.
    MRQ PE is retained separately in raw, never substituted for TTM PE. A record
    is not proof that turnover, volume ratio, capital or every ratio is present.
    Late projection has late availability even when the raw fact was captured earlier.
    """
    now = aware_time(projected_at)
    effective, captured = aware_time(evidence.get("effective_at")), aware_time(evidence.get("available_at"))
    symbol = str(evidence.get("symbol") or "").upper()
    payload = evidence.get("normalized")
    if now is None or effective is None or captured is None or not EQUITY.fullmatch(symbol):
        return None
    if not isinstance(payload, Mapping) or str(payload.get("ts_code") or "").upper() != symbol:
        return None
    local = effective.astimezone(CN_TZ)
    if local.date() != day or local.time() < time(15) or effective > captured or captured > now:
        return None
    if payload.get("trade_date") and str(payload["trade_date"]) != day.isoformat():
        return None
    pe, pb = ratio(payload.get("pe_ttm")), ratio(payload.get("pb_mrq"))
    if pe is None and pb is None:
        return None
    return {
        "symbol": symbol, "trading_date": day, "pe": pe, "pb": pb,
        "provider": "fuyao_ths", "available_at": max(now, captured),
        "raw": {
            "projection_version": VERSION, "capability": "a_share_valuations_snapshot",
            "source_effective_at": effective.isoformat(), "source_available_at": captured.isoformat(),
            "source_payload_sha256": evidence.get("payload_sha256"),
            "date_basis": payload.get("date_basis") or "persisted_effective_session_date",
            "field_basis": {"pe": "pe_ttm", "pb": "pb_mrq"},
            "source_values": {key: payload.get(key) if ratio(payload.get(key)) is not None else None for key in SOURCE_FIELDS},
            "coverage_note": "valuation_only_not_complete_daily_basic",
        },
    }
