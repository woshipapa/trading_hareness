"""Preview and project existing post-close valuation evidence, without provider I/O or DDL."""
from __future__ import annotations

import time as monotonic_time
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from .datasources.derived.daily_valuations import VERSION, projection_row

CN_TZ = ZoneInfo("Asia/Shanghai")

# Only actual, dated listed-equity bars can receive a projection. Existing
# providers' records remain intact. One document per symbol bounds the result.
SOURCE_SQL = """WITH bars AS MATERIALIZED (
    SELECT DISTINCT bar.symbol FROM quant.canonical_bars_daily bar
    JOIN quant.universe_membership_history membership ON membership.symbol=bar.symbol
      AND membership.universe_key='all_a' AND membership.effective_from<=%(day)s
      AND (membership.effective_to IS NULL OR membership.effective_to>%(day)s)
    WHERE bar.trading_date=%(day)s AND bar.quality_status='fresh' AND bar.available_at<=%(now)s
), gaps AS MATERIALIZED (
    SELECT bars.symbol FROM bars WHERE NOT EXISTS (
        SELECT 1 FROM quant.daily_fundamentals basic
        WHERE basic.symbol=bars.symbol AND basic.trading_date=%(day)s
          AND basic.available_at<=%(now)s)
)
SELECT DISTINCT ON (raw.symbol) raw.symbol,raw.effective_at,raw.available_at,
       raw.payload_sha256,raw.normalized
FROM quant.raw_market_observations raw JOIN gaps ON gaps.symbol=raw.symbol
WHERE raw.provider_key='fuyao_ths' AND raw.capability='a_share_valuations_snapshot'
  AND raw.effective_at>=%(close)s AND raw.effective_at<%(end)s AND raw.available_at<=%(now)s
ORDER BY raw.symbol,raw.effective_at DESC,raw.available_at DESC,raw.payload_sha256"""

COVERAGE_SQL = """WITH bars AS MATERIALIZED (
    SELECT DISTINCT bar.symbol FROM quant.canonical_bars_daily bar
    JOIN quant.universe_membership_history membership ON membership.symbol=bar.symbol
      AND membership.universe_key='all_a' AND membership.effective_from<=%(day)s
      AND (membership.effective_to IS NULL OR membership.effective_to>%(day)s)
    WHERE bar.trading_date=%(day)s AND bar.quality_status='fresh' AND bar.available_at<=%(now)s
), basic AS MATERIALIZED (
    SELECT b.symbol,bool_or(b.pe IS NOT NULL AND b.pe::text NOT IN ('NaN','Infinity','-Infinity')) AS has_pe,
      bool_or(b.pb IS NOT NULL AND b.pb::text NOT IN ('NaN','Infinity','-Infinity')) AS has_pb,
      bool_or(b.turnover_rate IS NOT NULL) AS has_turnover_rate,
      bool_or(b.volume_ratio IS NOT NULL) AS has_volume_ratio,
      bool_or(b.available_at<%(end)s) AS existed_by_session_end
    FROM quant.daily_fundamentals b JOIN bars ON bars.symbol=b.symbol
    WHERE b.trading_date=%(day)s AND b.available_at<=%(now)s GROUP BY b.symbol
)
SELECT split_part(bars.symbol,'.',2) AS exchange,count(*)::int AS bar_symbols,
    count(basic.symbol)::int AS record_symbols,
    count(*) FILTER (WHERE basic.has_pe)::int AS pe_symbols,
    count(*) FILTER (WHERE basic.has_pb)::int AS pb_symbols,
    count(*) FILTER (WHERE basic.has_turnover_rate)::int AS turnover_rate_symbols,
    count(*) FILTER (WHERE basic.has_volume_ratio)::int AS volume_ratio_symbols,
    count(*) FILTER (WHERE basic.existed_by_session_end)::int AS original_session_record_symbols
FROM bars LEFT JOIN basic ON basic.symbol=bars.symbol GROUP BY 1 ORDER BY 1"""

INSERT_SQL = """INSERT INTO quant.daily_fundamentals
    (symbol,trading_date,pe,pb,provider,available_at,raw)
SELECT p.symbol,p.trading_date,p.pe,p.pb,'fuyao_ths',p.available_at,p.raw
FROM jsonb_to_recordset(%s::jsonb) AS p(symbol text,trading_date date,pe numeric,pb numeric,
    available_at timestamptz,raw jsonb)
WHERE NOT EXISTS (SELECT 1 FROM quant.daily_fundamentals existing
    WHERE existing.symbol=p.symbol AND existing.trading_date=p.trading_date)
ON CONFLICT(symbol,trading_date,provider) DO NOTHING"""


def parameters(day: date, now: datetime) -> dict[str, Any]:
    if now.tzinfo is None:
        raise ValueError("projection time must be timezone-aware")
    return {"day": day, "now": now, "close": datetime.combine(day, time(15), CN_TZ),
            "end": datetime.combine(day + timedelta(days=1), time(), CN_TZ)}


def coverage(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    exchanges = [dict(row) for row in connection.execute(COVERAGE_SQL, params).fetchall()]
    total = {key: sum(row[key] for row in exchanges) for key in (
        "bar_symbols", "record_symbols", "pe_symbols", "pb_symbols", "turnover_rate_symbols",
        "volume_ratio_symbols", "original_session_record_symbols")}
    return {"exchanges": exchanges, "total": total,
            "records_complete": total["bar_symbols"] > 0 and total["bar_symbols"] == total["record_symbols"],
            "missing_records": total["bar_symbols"] - total["record_symbols"]}


def project_valuations(database: Any, day: date, *, apply: bool = False,
                       projected_at: datetime | None = None) -> dict[str, Any]:
    """Dry-run by default; a transaction-level lock serializes valuation projections.

    This lock is not a substitute for the proposed cross-stage writer lease.
    Automatic wiring stays opt-in until both hosts adopt that contract.
    """
    now = projected_at or datetime.now(timezone.utc)
    params = parameters(day, now)
    deadline = monotonic_time.monotonic() + 60
    with database.transaction() as connection:
        connection.execute("SET LOCAL statement_timeout='10s'")
        if apply:
            locked = connection.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS acquired",
                (f"daily-valuation-projection-v1:{day}",),
            ).fetchone()
            if not locked["acquired"]:
                return {"status": "blocked", "reason": "valuation projection already running", "trade_date": str(day)}
        before = coverage(connection, params)
        evidence = connection.execute(SOURCE_SQL, params).fetchall()
        rows = [item for record in evidence if (item := projection_row(record, day, now)) is not None]
        inserted = 0
        if apply and rows:
            payload = [{"symbol": row["symbol"], "trading_date": str(day), "pe": str(row["pe"]) if row["pe"] is not None else None,
                        "pb": str(row["pb"]) if row["pb"] is not None else None,
                        "available_at": row["available_at"].isoformat(), "raw": row["raw"]} for row in rows]
            inserted = connection.execute(INSERT_SQL, (Jsonb(payload),)).rowcount
        after = coverage(connection, params) if apply else before
        if monotonic_time.monotonic() >= deadline:
            raise TimeoutError("valuation projection exceeded transaction budget")
    return {"status": ("completed" if after["records_complete"] else "partial") if apply else "preview",
            "trade_date": str(day), "as_of": now.isoformat(), "methodology_version": VERSION,
            "apply": apply, "candidate_symbols": len(rows), "rejected_evidence": len(evidence) - len(rows),
            "inserted": inserted, "coverage_before": before, "coverage_after": after,
            "candidate_sample": [row["symbol"] for row in rows[:20]],
            "notice": "Record coverage is not field completeness; late repairs do not become session-time evidence."}
