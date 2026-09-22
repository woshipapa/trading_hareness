"""SQL for teacher-review packs; only existing, declared relations are touched.

* packs and settlements -> ``quant.raw_market_observations`` (provider
  ``teacher_review``), so no owner DDL is required;
* per-session plans -> ``quant.intraday_watchlists.metadata.teacher_review``,
  merged into the row without rewriting any other column or metadata key;
* scan outcomes are read back from ``quant.intraday_signal_events``.

Market data is read only from evidence the data-source layer already stored
(capabilities ``bars.daily`` -> ``canonical_bars_daily`` with point-in-time
``daily_adjustment_factors``, ``bars.minute`` -> ``intraday_minute_sessions``,
``limits.limit_up_pool`` -> ``market_events``, ``reference.trade_calendar``);
this strategy never calls a vendor.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from psycopg.types.json import Json

from .adjustment_factor_semantics import persisted_factor_semantics_sql
from .instrument_registry import InstrumentRecord, ensure_instruments
from .owner_factor_repository import FACTOR_PROVIDER_ORDER
from .research_prices import adjusted_bars
from .teacher_review_playbooks import ts_code

PROVIDER = "teacher_review"
PACK_CAPABILITY = "teacher_review_pack"
SETTLEMENT_CAPABILITY = "teacher_review_settlement"
OUTCOME_CAPABILITY = "teacher_review_outcome"
SOURCE_TAG = "teacher_review"
_CN_TZ = ZoneInfo("Asia/Shanghai")


def _insert_observation(connection: Any, capability: str, symbol: str | None, effective_at: datetime,
                        available_at: datetime, payload: dict[str, Any]) -> bool:
    body = {**payload, "provider_key": PROVIDER, "capability": capability}
    serialized = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
    row = connection.execute(
        """INSERT INTO quant.raw_market_observations(provider_key,capability,market,symbol,effective_at,available_at,payload_sha256,normalized,payload)
           VALUES(%s,%s,'cn',%s,%s,%s,%s,%s,%s)
           ON CONFLICT(provider_key,capability,market,symbol,effective_at,payload_sha256) DO NOTHING
           RETURNING observation_id""",
        (PROVIDER, capability, symbol, effective_at, available_at,
         hashlib.sha256(serialized.encode()).hexdigest(), Json(body), Json(body)),
    ).fetchone()
    return row is not None


def pack_record(database: Any, pack_id: str) -> dict[str, Any] | None:
    with database.transaction() as connection:
        row = connection.execute(
            """SELECT payload,available_at FROM quant.raw_market_observations
                WHERE provider_key=%s AND capability=%s AND payload->'pack'->>'pack_id'=%s
                ORDER BY available_at LIMIT 1""",
            (PROVIDER, PACK_CAPABILITY, pack_id),
        ).fetchone()
    return dict(row) if row else None


def persist_pack(database: Any, pack: dict[str, Any], *, review_close: datetime, available_at: datetime,
                 import_summary: dict[str, Any]) -> bool:
    with database.transaction() as connection:
        analyst = str((pack.get("analyst") or {}).get("analyst_id") or "unknown")
        return _insert_observation(
            connection, PACK_CAPABILITY, f"analyst:{analyst}", review_close, available_at,
            {"pack": pack, "import": import_summary, "research_only": True, "live_effect": "none"},
        )


def recent_packs(database: Any, *, since: date) -> list[dict[str, Any]]:
    """Packs whose review date is on/after ``since``, one row per pack id (first import wins)."""
    since_at = datetime.combine(since, time(0), tzinfo=_CN_TZ)
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT DISTINCT ON (payload->'pack'->>'pack_id') payload,available_at
                 FROM quant.raw_market_observations
                WHERE provider_key=%s AND capability=%s AND effective_at>=%s
                ORDER BY payload->'pack'->>'pack_id',available_at""",
            (PROVIDER, PACK_CAPABILITY, since_at),
        ).fetchall()
    return [{"pack": row["payload"]["pack"], "available_at": row["available_at"]} for row in rows]


def open_sessions(database: Any, *, after: date, count: int = 8) -> list[date]:
    """Next SSE sessions strictly after ``after`` from the point-in-time calendar."""
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT DISTINCT calendar_date FROM quant.market_trade_calendar
                WHERE exchange='SSE' AND is_open AND calendar_date>%s
                ORDER BY calendar_date LIMIT %s""",
            (after, count),
        ).fetchall()
    return [row["calendar_date"] for row in rows]


def sessions_between(database: Any, *, after: date, through: date) -> list[date]:
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT DISTINCT calendar_date FROM quant.market_trade_calendar
                WHERE exchange='SSE' AND is_open AND calendar_date>%s AND calendar_date<=%s
                ORDER BY calendar_date""",
            (after, through),
        ).fetchall()
    return [row["calendar_date"] for row in rows]


def teacher_watch_rows(database: Any) -> list[dict[str, Any]]:
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT watchlist_id,symbol,label,enabled,metadata FROM quant.intraday_watchlists
                WHERE metadata ? 'teacher_review' ORDER BY symbol""",
        ).fetchall()
    return [dict(row) for row in rows]


def apply_session_plans(
    database: Any, plans: Iterable[dict[str, Any]], *, max_symbols: int, reserve: int,
    exchange_for: Any,
) -> dict[str, Any]:
    """Merge each plan into its watch row; admit new rows only within capacity.

    ``plans`` must be ordered by admission priority.  An existing row keeps its
    label, flags, prices and every other metadata key; only
    ``metadata.teacher_review`` is replaced.  A new row is created enabled,
    tagged ``metadata.source='teacher_review'`` so retirement never touches a
    hand-added name.  The enabled count is re-read under a table lock so a
    concurrent writer cannot push the scan over its fail-closed cap.
    """
    admitted, merged, overflow, skipped = [], [], [], []
    new_rows: list[dict[str, Any]] = []
    with database.transaction() as connection:
        connection.execute("LOCK TABLE quant.intraday_watchlists IN SHARE ROW EXCLUSIVE MODE")
        enabled = int(connection.execute(
            "SELECT count(*)::int AS n FROM quant.intraday_watchlists WHERE enabled",
        ).fetchone()["n"])
        headroom = max(0, int(max_symbols) - int(reserve) - enabled)
        for plan in plans:
            symbol = str(plan["ts_code"])
            existing = connection.execute(
                "SELECT watchlist_id,enabled,metadata FROM quant.intraday_watchlists WHERE symbol=%s FOR UPDATE",
                (symbol,),
            ).fetchone()
            if existing is not None:
                metadata = dict(existing["metadata"] or {})
                ours = metadata.get("source") == SOURCE_TAG
                if not existing["enabled"] and not ours:
                    skipped.append({"symbol": symbol, "reason": "disabled_by_other_source"})
                    continue
                if not existing["enabled"]:
                    if headroom <= 0:
                        overflow.append(symbol)
                        continue
                    headroom -= 1
                connection.execute(
                    """UPDATE quant.intraday_watchlists
                          SET metadata=jsonb_set(COALESCE(metadata,'{}'::jsonb),'{teacher_review}',%s::jsonb,true),
                              enabled=CASE WHEN metadata->>'source'=%s THEN true ELSE enabled END,
                              updated_at=now()
                        WHERE symbol=%s""",
                    (Json(plan["metadata"]), SOURCE_TAG, symbol),
                )
                merged.append(symbol)
                continue
            if headroom <= 0:
                overflow.append(symbol)
                continue
            ensure_instruments(connection, [InstrumentRecord(
                symbol=symbol, exchange=exchange_for(symbol), name=plan["name"], source="teacher_review",
            )], source="teacher_review")
            row = connection.execute(
                """INSERT INTO quant.intraday_watchlists(symbol,label,enabled,alert_on_entry,alert_on_exit,metadata)
                   VALUES(%s,%s,true,true,true,%s) RETURNING watchlist_id""",
                (symbol, plan["label"], Json({"source": SOURCE_TAG, "teacher_review": plan["metadata"]})),
            ).fetchone()
            headroom -= 1
            admitted.append(symbol)
            new_rows.append({"watchlist_id": row["watchlist_id"], "symbol": symbol})
    return {"admitted": admitted, "merged": merged, "overflow": overflow, "skipped": skipped,
            "new_rows": new_rows, "max_symbols": max_symbols, "reserve": reserve}


def retire_plans(database: Any, *, keep: set[str], retired_at: datetime,
                 only_pack_ids: set[str] | None = None, only_symbols: set[str] | None = None) -> dict[str, Any]:
    """Expire teacher plans not in ``keep``: disable our own rows, strip the key from others.

    ``only_pack_ids`` limits it to plans of those packs (a superseded pack);
    ``only_symbols`` to those stocks (a newer review now rejects them).
    """
    disabled, stripped = [], []
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT symbol,metadata FROM quant.intraday_watchlists
                WHERE metadata ? 'teacher_review' FOR UPDATE""",
        ).fetchall()
        for row in rows:
            symbol = str(row["symbol"])
            if symbol in keep:
                continue
            metadata = dict(row["metadata"] or {})
            if only_pack_ids is not None and str((metadata.get("teacher_review") or {}).get("pack_id")) not in only_pack_ids:
                continue
            if only_symbols is not None and symbol not in only_symbols:
                continue
            if metadata.get("source") == SOURCE_TAG:
                history = dict(metadata.get("teacher_review") or {})
                history.update({"status": "expired", "expired_at": retired_at.isoformat()})
                connection.execute(
                    """UPDATE quant.intraday_watchlists
                          SET enabled=false,metadata=jsonb_set(metadata,'{teacher_review}',%s::jsonb,true),updated_at=now()
                        WHERE symbol=%s""",
                    (Json(history), symbol),
                )
                disabled.append(symbol)
            else:
                connection.execute(
                    "UPDATE quant.intraday_watchlists SET metadata=metadata-'teacher_review',updated_at=now() WHERE symbol=%s",
                    (symbol,),
                )
                stripped.append(symbol)
    return {"disabled": disabled, "stripped": stripped}


def session_events(database: Any, session_date: date) -> list[dict[str, Any]]:
    start = datetime.combine(session_date, time(9, 0), tzinfo=_CN_TZ).astimezone(timezone.utc)
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT symbol,signal_key,signal_type,state,observed_at,conditions->'teacher_review' AS review
                 FROM quant.intraday_signal_events
                WHERE observed_at>=%s AND observed_at<%s AND conditions ? 'teacher_review'
                ORDER BY observed_at""",
            (start, start + timedelta(hours=7)),
        ).fetchall()
    return [dict(row) for row in rows]


def persist_settlement(database: Any, session_date: date, settlement: dict[str, Any], *, available_at: datetime) -> bool:
    effective = datetime.combine(session_date, time(15, 0), tzinfo=_CN_TZ)
    with database.transaction() as connection:
        return _insert_observation(connection, SETTLEMENT_CAPABILITY, "teacher_review:session", effective,
                                   available_at, settlement)


def recent_settlements(database: Any, *, limit: int = 10) -> list[dict[str, Any]]:
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT effective_at,available_at,payload FROM quant.raw_market_observations
                WHERE provider_key=%s AND capability=%s ORDER BY effective_at DESC,available_at DESC LIMIT %s""",
            (PROVIDER, SETTLEMENT_CAPABILITY, max(1, min(int(limit), 60))),
        ).fetchall()
    return [dict(row) for row in rows]


def persist_outcome_review(database: Any, session_date: date, report: dict[str, Any], *,
                           available_at: datetime) -> bool:
    """One archived learning report per session (see ``teacher_outcome_review``)."""
    effective = datetime.combine(session_date, time(15, 0), tzinfo=_CN_TZ)
    with database.transaction() as connection:
        return _insert_observation(connection, OUTCOME_CAPABILITY, "teacher_review:outcome", effective,
                                   available_at, report)


def recent_outcome_reviews(database: Any, *, limit: int = 20) -> list[dict[str, Any]]:
    """Newest archived report per session, newest first (a re-run adds a row)."""
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT DISTINCT ON (effective_at) effective_at,available_at,payload
                 FROM quant.raw_market_observations
                WHERE provider_key=%s AND capability=%s
                ORDER BY effective_at DESC,available_at DESC LIMIT %s""",
            (PROVIDER, OUTCOME_CAPABILITY, max(1, min(int(limit), 60))),
        ).fetchall()
    return [dict(row) for row in rows]


#: One replayed scan every this many seconds is enough to count what blocked a
#: plan; the live scan samples about twice a minute and each bundle is large.
REPLAY_BUCKET_SECONDS = 120


def rule_input_snapshots(database: Any, codes: Iterable[str], trade_date: date,
                         *, bucket_seconds: int = REPLAY_BUCKET_SECONDS) -> dict[str, list[dict[str, Any]]]:
    """Frozen scan inputs for one session, thinned to one row per time bucket.

    Only the fields the teacher rules read are projected: the stored bundle
    also carries policy, portfolio and market context this replay never
    touches, and reading all of it for a whole pool would be hundreds of MB.
    Keys are six-digit codes, matching the settlement payload.
    """
    symbols = sorted({ts_code(str(code)) for code in codes if str(code)})
    if not symbols:
        return {}
    start = datetime.combine(trade_date, time(9, 15), tzinfo=_CN_TZ)
    end = datetime.combine(trade_date, time(15, 5), tzinfo=_CN_TZ)
    bucket = "to_timestamp(floor(extract(epoch FROM observed_at)/%s)*%s)"
    with database.transaction() as connection:
        rows = connection.execute(
            f"""SELECT DISTINCT ON (symbol, {bucket})
                       symbol,observed_at,
                       jsonb_build_object(
                           'watch', inputs->'watch', 'quote', inputs->'quote',
                           'previous_quote', inputs->'previous_quote',
                           'minute_features', inputs->'minute_features') AS inputs
                  FROM quant.intraday_rule_input_snapshots
                 WHERE symbol=ANY(%s) AND observed_at>=%s AND observed_at<%s
                 ORDER BY symbol, {bucket}, observed_at""",
            (bucket_seconds, bucket_seconds, symbols, start, end, bucket_seconds, bucket_seconds),
        ).fetchall()
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(str(row["symbol"])[:6], []).append(
            {"observed_at": row["observed_at"], "inputs": row["inputs"]})
    for items in result.values():
        items.sort(key=lambda item: item["observed_at"])
    return result


#: Sessions whose bars must all be present (or explicitly suspended) before a
#: plan is frozen; covers MA60, the 20-session platform and prior highs.
GAP_CHECK_SESSIONS = 60


def calendar_gaps(bar_dates: set[date], suspended: set[date], sessions: Iterable[date]) -> list[date]:
    """Open sessions with neither a usable bar nor a suspension record, newest first.

    Sessions before the stock's first stored bar (a recent listing) are not gaps.
    """
    if not bar_dates:
        return []
    first = min(bar_dates)
    return sorted((day for day in sessions if day >= first and day not in bar_dates and day not in suspended),
                  reverse=True)


def plan_bars(database: Any, symbols: Iterable[str], *, through: date, limit: int = 260) -> dict[str, dict[str, Any]]:
    """Adjusted daily bars through ``through`` (inclusive), anchored to its close.

    Research prices come from the shared point-in-time factor contract
    (``research_prices.adjusted_bars``) and are divided by the last session's
    factor, so levels share the scale of the next session's raw quotes.  A
    symbol without a complete factor window is reported, never priced raw.
    """
    requested = sorted({str(symbol).upper() for symbol in symbols if str(symbol)})
    if not requested:
        return {}
    factor_sql = persisted_factor_semantics_sql("factor")
    with database.transaction() as connection:
        rows = connection.execute(
            f"""WITH ranked AS (
                   SELECT b.symbol,b.trading_date,b.open,b.high,b.low,b.close,b.volume,b.amount,b.limit_up,b.is_suspended,
                          pit.adj_factor,pit.provider AS factor_provider,pit.raw AS factor_raw,
                          row_number() OVER(PARTITION BY b.symbol ORDER BY b.trading_date DESC) AS row_number
                     FROM quant.canonical_bars_daily b
                     JOIN LATERAL (
                           SELECT factor.adj_factor,factor.provider,factor.raw
                             FROM quant.daily_adjustment_factors factor
                            WHERE factor.symbol=b.symbol AND factor.trading_date=b.trading_date
                              AND {factor_sql} AND factor.adj_factor>0
                              -- A plan is frozen now for the next session, so a factor
                              -- is usable once it is known now; a late backfill of an
                              -- older date is still known before the session opens.
                              AND factor.available_at<=now()
                            ORDER BY array_position(%s::text[],factor.provider) NULLS LAST,
                                     factor.available_at DESC,factor.provider
                            LIMIT 1
                     ) pit ON TRUE
                    WHERE b.symbol=ANY(%s) AND b.trading_date<=%s
                      -- owner marks many complete OHLC rows 'partial' (a missing
                      -- auxiliary field); prices are usable, so only require them.
                      AND b.quality_status IN ('fresh','partial')
                      AND b.open IS NOT NULL AND b.high IS NOT NULL AND b.low IS NOT NULL AND b.close IS NOT NULL
                      AND b.available_at<=now() AND b.volume>0 AND NOT coalesce(b.is_suspended,false)
               )
               SELECT * FROM ranked WHERE row_number<=%s ORDER BY symbol,trading_date""",
            (list(FACTOR_PROVIDER_ORDER), requested, through, int(limit)),
        ).fetchall()
        sessions = [row["calendar_date"] for row in connection.execute(
            """SELECT DISTINCT calendar_date FROM quant.market_trade_calendar
                WHERE exchange='SSE' AND is_open AND calendar_date<=%s ORDER BY calendar_date DESC LIMIT %s""",
            (through, GAP_CHECK_SESSIONS),
        ).fetchall()]
        suspended = {(str(row["symbol"]), row["trading_date"]) for row in connection.execute(
            """SELECT symbol,trading_date FROM quant.canonical_bars_daily
                WHERE symbol=ANY(%s) AND trading_date<=%s AND trading_date>=%s
                  AND (coalesce(is_suspended,false) OR coalesce(volume,0)=0)""",
            (requested, through, min(sessions) if sessions else through),
        ).fetchall()}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["symbol"]), []).append(dict(row))
    result: dict[str, dict[str, Any]] = {}
    for symbol in requested:
        raw_rows = grouped.get(symbol) or []
        gaps = calendar_gaps({row["trading_date"] for row in raw_rows},
                             {day for sym, day in suspended if sym == symbol}, sessions)
        if gaps:
            result[symbol] = {"status": "unavailable", "flags": ["bar_gaps:" + ",".join(str(day) for day in gaps[:5])]}
            continue
        prepared, flags = adjusted_bars(raw_rows)
        if not raw_rows or prepared is None:
            result[symbol] = {"status": "unavailable", "flags": flags or ["no_bars"]}
            continue
        anchor = float(prepared[-1]["adj_factor"])
        bars = [{
            "date": row["trading_date"].strftime("%Y%m%d"),
            **{field: round(float(row[f"research_{field}"]) / anchor, 4) for field in ("open", "high", "low", "close")},
            # canonical volume is 手 and amount is 千元
            "volume_lot": float(row["volume"] or 0), "amount": float(row["amount"] or 0) * 1000,
            "limit_up": row["limit_up"] is not None and float(row["close"]) >= float(row["limit_up"]) - 0.005,
        } for row in prepared]
        result[symbol] = {"status": "ok", "bars": bars, "raw_close": float(raw_rows[-1]["close"]),
                          "trading_date": raw_rows[-1]["trading_date"]}
    return result


_BUCKET_ENDS = {
    "30": ("10:00", "10:30", "11:00", "11:30", "13:30", "14:00", "14:30", "15:00"),
    "60": ("10:30", "11:30", "14:00", "15:00"),
}


def minute_period_bars(database: Any, symbol: str, *, through: date, period: str, sessions: int = 12) -> list[dict[str, Any]]:
    """Aggregate stored minute evidence into 30/60-minute bars (one source per minute)."""
    ends = _BUCKET_ENDS[period]
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT DISTINCT ON (trading_date,minute_bucket) trading_date,minute_bucket,open,high,low,close,volume
                 FROM quant.intraday_minute_sessions
                WHERE symbol=%s AND trading_date<=%s AND trading_date>%s::date-%s*2
                ORDER BY trading_date,minute_bucket,source_name""",
            (symbol, through, through, int(sessions)),
        ).fetchall()
    buckets: dict[tuple[Any, str], dict[str, Any]] = {}
    for row in rows:
        minute = str(row["minute_bucket"])
        end = next((value for value in ends if minute <= value), None)
        if end is None:
            continue
        key = (row["trading_date"], end)
        high, low, close = float(row["high"]), float(row["low"]), float(row["close"])
        bucket = buckets.get(key)
        if bucket is None:
            buckets[key] = {"date": f"{row['trading_date'].strftime('%Y%m%d')}{end.replace(':', '')}",
                            "open": float(row["open"]), "high": high, "low": low, "close": close}
        else:
            bucket.update(high=max(bucket["high"], high), low=min(bucket["low"], low), close=close)
    return [buckets[key] for key in sorted(buckets)]


def latest_limit_up_pool(database: Any, *, since: datetime) -> tuple[datetime | None, list[dict[str, Any]]]:
    """The newest stored limit-up pool snapshot (Fuyao, minute cadence) at or after ``since``."""
    with database.transaction() as connection:
        rows = connection.execute(
            """WITH latest AS (
                   SELECT max(occurred_at) AS at FROM quant.market_events
                    WHERE event_type='limit_up_pool' AND source='fuyao_ths' AND occurred_at>=%s)
               SELECT e.symbol,e.body,e.occurred_at FROM quant.market_events e, latest
                WHERE e.event_type='limit_up_pool' AND e.source='fuyao_ths' AND e.occurred_at=latest.at""",
            (since,),
        ).fetchall()
    result = []
    for row in rows:
        try:
            body = json.loads(row["body"]) if isinstance(row["body"], str) else dict(row["body"] or {})
        except (TypeError, ValueError):
            body = {}
        result.append({"symbol": row["symbol"], "name": body.get("name"), "limit_up_reason": body.get("limit_up_reason"),
                       "limit_up_time": body.get("limit_up_time")})
    return (rows[0]["occurred_at"] if rows else None), result


def session_bars(database: Any, symbols: Iterable[str], trade_date: date) -> dict[str, dict[str, Any]]:
    """Raw settled bars (execution facts) for one session, including the exact limit price."""
    requested = sorted({str(symbol).upper() for symbol in symbols if str(symbol)})
    if not requested:
        return {}
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT b.symbol,b.open,b.high,b.low,b.close,b.pre_close,b.amount,
                      coalesce(limits.limit_up,b.limit_up) AS limit_up
                 FROM quant.canonical_bars_daily b
                 LEFT JOIN quant.daily_trade_limits limits
                   ON limits.symbol=b.symbol AND limits.trading_date=b.trading_date
                WHERE b.symbol=ANY(%s) AND b.trading_date=%s AND b.close IS NOT NULL""",
            (requested, trade_date),
        ).fetchall()
    result = {}
    for row in rows:
        close, pre_close = float(row["close"]), float(row["pre_close"]) if row["pre_close"] else None
        result[str(row["symbol"])] = {
            "open": float(row["open"]) if row["open"] is not None else None, "high": float(row["high"]),
            "low": float(row["low"]), "close": close, "pre_close": pre_close,
            "pct": round((close / pre_close - 1) * 100, 2) if pre_close else None,
            "amount": float(row["amount"] or 0) * 1000,
            "limit_up_price": float(row["limit_up"]) if row["limit_up"] is not None else None,
        }
    return result


def first_limit_up_times(database: Any, symbols: Iterable[str], trade_date: date) -> dict[str, datetime]:
    """First intraday limit-up pool observation per symbol (minute-resolution capture)."""
    start = datetime.combine(trade_date, time(9, 0), tzinfo=_CN_TZ)
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT symbol,min(occurred_at) AS first_at FROM quant.market_events
                WHERE event_type='limit_up_pool' AND symbol=ANY(%s)
                  AND occurred_at>=%s AND occurred_at<%s
                GROUP BY symbol""",
            (sorted({str(s).upper() for s in symbols}), start,
             datetime.combine(trade_date, time(15, 0), tzinfo=_CN_TZ)),
        ).fetchall()
    return {str(row["symbol"]): row["first_at"] for row in rows}


def xiaojie_session_modes(database: Any, trade_date: date) -> dict[str, list[str]]:
    """Leader-flow modes (incl. 潜龙出海) each symbol entered on one session."""
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT symbol,array_agg(DISTINCT mode ORDER BY mode) AS modes
                 FROM quant.xiaojie_leader_flow_observations WHERE trading_date=%s GROUP BY symbol""",
            (trade_date,),
        ).fetchall()
    return {str(row["symbol"]): list(row["modes"] or []) for row in rows}


__all__ = [
    "GAP_CHECK_SESSIONS", "OUTCOME_CAPABILITY", "PACK_CAPABILITY", "PROVIDER", "REPLAY_BUCKET_SECONDS",
    "SETTLEMENT_CAPABILITY", "SOURCE_TAG", "apply_session_plans",
    "calendar_gaps", "first_limit_up_times",
    "minute_period_bars", "open_sessions", "pack_record", "persist_pack", "persist_settlement", "plan_bars",
    "persist_outcome_review", "recent_outcome_reviews", "recent_packs", "recent_settlements", "retire_plans",
    "rule_input_snapshots", "session_bars", "session_events", "sessions_between",
    "latest_limit_up_pool", "teacher_watch_rows", "xiaojie_session_modes",
]
