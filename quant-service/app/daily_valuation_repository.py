"""Preview and project existing post-close valuation evidence, without provider I/O or DDL."""
from __future__ import annotations

import time as monotonic_time
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from .datasources.derived.daily_valuations import VERSION, projection_row

CN_TZ = ZoneInfo("Asia/Shanghai")
#: One budget for the projection's transaction, under the collector's 90 s executor timeout, so the
#: work ends before its caller gives up; each statement's timeout is the smaller of its own cap and
#: what is left. The single 10 s limit this replaces cancelled the evidence scan twice on a cold cache
#: after a restart (2026-10-10, 166,740 snapshot rows), while it takes about 2 s warm.
TRANSACTION_BUDGET_SECONDS = 60.0
STATEMENT_CAPS = {"coverage_before": 15.0, "evidence": 45.0, "insert": 20.0, "coverage_after": 15.0}

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


class StatementBudget:
    """Times each statement and bounds it by its cap and by the transaction's remaining budget."""

    def __init__(self, seconds: float = TRANSACTION_BUDGET_SECONDS, clock: Any = monotonic_time.monotonic) -> None:
        self.seconds, self.clock = seconds, clock
        self.deadline = clock() + seconds
        self.timings: dict[str, float] = {}

    def execute(self, connection: Any, name: str, sql: str, params: Any) -> Any:
        from psycopg.errors import QueryCanceled  # noqa: PLC0415

        remaining = self.deadline - self.clock()
        if remaining < 0.5:
            raise TimeoutError(f"valuation projection spent its {self.seconds:.0f}s budget before {name}")
        limit = min(STATEMENT_CAPS[name], remaining)
        connection.execute(f"SET LOCAL statement_timeout = '{int(limit * 1000)}ms'")
        started = self.clock()
        try:
            return connection.execute(sql, params)
        except QueryCanceled as error:
            raise TimeoutError(f"valuation projection: {name} exceeded its {limit:.1f}s statement limit") from error
        finally:
            self.timings[name] = round(self.clock() - started, 3)


def coverage(connection: Any, params: dict[str, Any], *, budget: StatementBudget | None = None,
             name: str = "coverage_before") -> dict[str, Any]:
    cursor = budget.execute(connection, name, COVERAGE_SQL, params) if budget else connection.execute(COVERAGE_SQL, params)
    exchanges = [dict(row) for row in cursor.fetchall()]
    total = {key: sum(row[key] for row in exchanges) for key in (
        "bar_symbols", "record_symbols", "pe_symbols", "pb_symbols", "turnover_rate_symbols",
        "volume_ratio_symbols", "original_session_record_symbols")}
    return {"exchanges": exchanges, "total": total,
            "records_complete": total["bar_symbols"] > 0 and total["bar_symbols"] == total["record_symbols"],
            "missing_records": total["bar_symbols"] - total["record_symbols"]}


def project_valuations(database: Any, day: date, *, apply: bool = False,
                       projected_at: datetime | None = None, budget: StatementBudget | None = None) -> dict[str, Any]:
    """Dry-run by default; a transaction-level lock serializes valuation projections.

    This lock is not a substitute for the proposed cross-stage writer lease.
    Automatic wiring stays opt-in until both hosts adopt that contract.
    """
    now = projected_at or datetime.now(timezone.utc)
    params = parameters(day, now)
    budget = budget or StatementBudget()
    with database.transaction() as connection:
        if apply:
            locked = connection.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS acquired",
                (f"daily-valuation-projection-v1:{day}",),
            ).fetchone()
            if not locked["acquired"]:
                return {"status": "blocked", "reason": "valuation projection already running", "trade_date": str(day)}
        before = coverage(connection, params, budget=budget, name="coverage_before")
        evidence = budget.execute(connection, "evidence", SOURCE_SQL, params).fetchall()
        rows = [item for record in evidence if (item := projection_row(record, day, now)) is not None]
        inserted = 0
        if apply and rows:
            payload = [{"symbol": row["symbol"], "trading_date": str(day), "pe": str(row["pe"]) if row["pe"] is not None else None,
                        "pb": str(row["pb"]) if row["pb"] is not None else None,
                        "available_at": row["available_at"].isoformat(), "raw": row["raw"]} for row in rows]
            inserted = budget.execute(connection, "insert", INSERT_SQL, (Jsonb(payload),)).rowcount
        after = coverage(connection, params, budget=budget, name="coverage_after") if apply else before
    return {"status": ("completed" if after["records_complete"] else "partial") if apply else "preview",
            "trade_date": str(day), "as_of": now.isoformat(), "methodology_version": VERSION,
            "apply": apply, "candidate_symbols": len(rows), "rejected_evidence": len(evidence) - len(rows),
            "inserted": inserted, "coverage_before": before, "coverage_after": after,
            "timings_seconds": budget.timings, "budget_seconds": budget.seconds,
            "candidate_sample": [row["symbol"] for row in rows[:20]],
            "notice": "Record coverage is not field completeness; late repairs do not become session-time evidence."}
