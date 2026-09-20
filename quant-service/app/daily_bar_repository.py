"""Canonical A-share daily-bar persistence, isolated from HTTP orchestration.

The functions here only operate on a caller-owned transaction or repository.
They do not own a provider client, invoke a market endpoint, or decide when a
sync should run.  Keeping raw evidence, canonical selection and quality
warnings together protects the P0 price-basis/control-plane invariants while
making the same write contract reusable by future offline replay.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from psycopg.types.json import Json

from .analysis import as_utc
from .adjustment_factor_semantics import positive_decimal
from .instrument_registry import InstrumentRecord, ensure_instruments
from .request_models import DailyBar


# Tushare's documented daily contract is ``vol`` in lots (100 shares) and
# ``amount`` in thousand yuan.  Its implied amount/(lots * close) ratio is
# therefore close to 0.1.  Some compatible GET responses have returned a
# small subset of rows with ``amount`` in yuan instead, a 1,000x mixture that
# silently corrupts liquidity ranks.  Keep the original row in immutable raw
# evidence, but never promote an unverified amount into either daily control
# table.
TUSHARE_DAILY_AMOUNT_SOURCES = frozenset({
    "tushare", "tushare_primary", "tushare_super_get", "tushare_super_sdk",
    "tushare_super", "tushare_backup",
})
TUSHARE_DAILY_AMOUNT_RATIO_MIN = Decimal("0.02")
TUSHARE_DAILY_AMOUNT_RATIO_MAX = Decimal("0.50")


def exchange_for(symbol: str) -> str:
    return symbol.rsplit(".", 1)[1]


def provider_priority(provider: str) -> int:
    return {
        "tushare": 10, "tushare_primary": 10, "tushare_super_get": 15,
        "tushare_super_sdk": 20, "tushare_super": 25,
        "longhuvip_composite": 25, "baostock": 30, "tushare_backup": 40, "eastmoney_free": 45,
        "tencent_index_free": 45,
        "akshare": 50, "tencent_free": 50, "sina_free": 55, "manual": 90,
    }.get(provider, 80)


def persisted_adjustment_state(bar: DailyBar) -> str:
    """Derive the bar-side state without promoting an unlicensed factor."""
    if positive_decimal(bar.adj_factor) is None:
        return "absent"
    source = str(bar.source or "")
    return "complete" if source.startswith("tushare") or source == "longhu_qfq_derived" else "pending"


def daily_amount_unit_mismatch(*, source: str, amount: Decimal | None,
                               volume: Decimal | None, close: Decimal | None) -> bool:
    """Return whether a Tushare daily amount violates its declared unit contract.

    A missing/zero field is not a unit conclusion.  We only quarantine a row
    when all three values exist and its implied VWAP is impossible under the
    documented ``amount=thousand yuan, volume=lots`` convention.  This is a
    data-quality guard, never an inferred currency conversion.
    """
    if source not in TUSHARE_DAILY_AMOUNT_SOURCES:
        return False
    if amount is None or volume is None or close is None or amount <= 0 or volume <= 0 or close <= 0:
        return False
    ratio = amount / (volume * close)
    return not TUSHARE_DAILY_AMOUNT_RATIO_MIN <= ratio <= TUSHARE_DAILY_AMOUNT_RATIO_MAX


def _record_daily_amount_unit_issue(connection: Any, bar: DailyBar) -> None:
    """Record one unresolved issue per symbol/date without duplicating retries."""
    implied_ratio = bar.amount / (bar.volume * bar.close)  # caller checked non-zero fields
    connection.execute(
        """INSERT INTO quant.data_quality_issues(capability,symbol,trading_date,severity,code,message,details)
           SELECT 'daily_bar',%s,%s,'warning','daily_amount_unit_mismatch',
                  'daily amount does not match the Tushare lots/thousand-yuan contract',%s
            WHERE NOT EXISTS (
                SELECT 1 FROM quant.data_quality_issues
                 WHERE capability='daily_bar' AND symbol=%s AND trading_date=%s
                   AND code='daily_amount_unit_mismatch' AND resolved_at IS NULL
            )""",
        (bar.symbol, bar.trading_date, Json({
            "provider": bar.source,
            "amount": str(bar.amount), "volume_lot": str(bar.volume),
            "close": str(bar.close), "implied_amount_per_lot_close": str(implied_ratio),
            "expected_ratio_range": [str(TUSHARE_DAILY_AMOUNT_RATIO_MIN), str(TUSHARE_DAILY_AMOUNT_RATIO_MAX)],
            "action": "amount_quarantined_not_rescaled",
        }), bar.symbol, bar.trading_date),
    )


def quarantine_tushare_daily_amount_mismatches(
    connection: Any, *, trading_dates: tuple[date, ...] | None = None,
) -> int:
    """Quarantine existing mixed-unit Tushare daily amounts without fetching data.

    This repair is idempotent: it only clears values that still violate the
    documented unit contract, preserves raw observations, and emits at most
    one unresolved issue for a symbol/date.  It intentionally does *not*
    divide by 1,000 because the gateway response does not provide a reliable
    per-row unit declaration.
    """
    date_filter = " AND trading_date=ANY(%s)" if trading_dates else ""
    params: list[Any] = [
        list(TUSHARE_DAILY_AMOUNT_SOURCES), TUSHARE_DAILY_AMOUNT_RATIO_MIN,
        TUSHARE_DAILY_AMOUNT_RATIO_MAX,
    ]
    if trading_dates:
        params.append(list(trading_dates))
    rows = connection.execute(
        """SELECT bar.symbol,bar.trading_date,bar.close,bar.selected_provider,
                  nullif(observation.normalized->>'amount','')::numeric AS amount,
                  nullif(observation.normalized->>'volume','')::numeric AS volume
             FROM quant.canonical_bars_daily bar
             JOIN LATERAL (
                 SELECT normalized FROM quant.raw_market_observations observation
                  WHERE observation.observation_id=ANY(bar.source_observation_ids)
                    AND observation.provider_key=bar.selected_provider
                  ORDER BY observation.available_at DESC,observation.created_at DESC
                  LIMIT 1
             ) observation ON true
            WHERE bar.selected_provider=ANY(%s) AND bar.close>0
              AND nullif(observation.normalized->>'amount','')::numeric>0
              AND nullif(observation.normalized->>'volume','')::numeric>0
              AND nullif(observation.normalized->>'amount','')::numeric /
                  (nullif(observation.normalized->>'volume','')::numeric * bar.close)
                  NOT BETWEEN %s AND %s""" + date_filter,
        params,
    ).fetchall()
    for row in rows:
        bar = DailyBar(
            symbol=row["symbol"], trading_date=row["trading_date"], close=Decimal(row["close"]),
            volume=Decimal(row["volume"]), amount=Decimal(row["amount"]),
            source=str(row["selected_provider"]),
        )
        _record_daily_amount_unit_issue(connection, bar)
        connection.execute(
            """UPDATE quant.canonical_bars_daily
                  SET amount=NULL,
                      quality_status=CASE WHEN quality_status='fresh' THEN 'partial' ELSE quality_status END,
                      canonicalized_at=now()
                WHERE symbol=%s AND trading_date=%s""",
            (bar.symbol, bar.trading_date),
        )
        connection.execute(
            """UPDATE quant.market_bars_daily SET amount=NULL
                WHERE symbol=%s AND trading_date=%s AND source=%s""",
            (bar.symbol, bar.trading_date, bar.source),
        )
    return len(rows)


def upsert_daily_bar(connection: Any, bar: DailyBar) -> None:
    """Persist one licensed/unadjusted daily bar and its immutable evidence."""
    if bar.source == "tencent_free":
        # Tencent's public adapter is qfq/front-adjusted.  It can be retained
        # as raw research evidence but must never enter the unadjusted series.
        raise ValueError("tencent_free front-adjusted daily rows are raw research evidence only")
    amount_mismatch = daily_amount_unit_mismatch(
        source=bar.source, amount=bar.amount, volume=bar.volume, close=bar.close,
    )
    promoted_amount = None if amount_mismatch else bar.amount
    ensure_instruments(connection, [InstrumentRecord(
        symbol=bar.symbol, exchange=exchange_for(bar.symbol), name=bar.name,
        industry=bar.industry, is_st=bar.is_st, source=bar.source,
    )], update_existing=True)
    connection.execute(
        """INSERT INTO quant.market_bars_daily(symbol,trading_date,open,high,low,close,pre_close,volume,amount,adj_factor,is_suspended,limit_up,limit_down,source,available_at)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,coalesce(%s,false),%s,%s,%s,%s)
           ON CONFLICT(symbol,trading_date) DO UPDATE SET open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,
             close=EXCLUDED.close,pre_close=EXCLUDED.pre_close,volume=EXCLUDED.volume,amount=EXCLUDED.amount,
             adj_factor=coalesce(EXCLUDED.adj_factor,quant.market_bars_daily.adj_factor),
             is_suspended=CASE WHEN %s::boolean IS NULL THEN quant.market_bars_daily.is_suspended ELSE EXCLUDED.is_suspended END,
             limit_up=coalesce(EXCLUDED.limit_up,quant.market_bars_daily.limit_up),
             limit_down=coalesce(EXCLUDED.limit_down,quant.market_bars_daily.limit_down),source=EXCLUDED.source,available_at=EXCLUDED.available_at""",
        (bar.symbol, bar.trading_date, bar.open, bar.high, bar.low, bar.close, bar.pre_close, bar.volume,
         promoted_amount, bar.adj_factor, bar.is_suspended, bar.limit_up, bar.limit_down, bar.source,
         as_utc(bar.available_at), bar.is_suspended),
    )
    normalized = bar.model_dump(mode="json")
    payload_sha256 = hashlib.sha256(repr(sorted(normalized.items())).encode("utf-8")).hexdigest()
    observation = connection.execute(
        """INSERT INTO quant.raw_market_observations(provider_key,capability,market,symbol,effective_at,available_at,payload_sha256,normalized,payload)
           VALUES(%s,'daily_bar','cn',%s,%s,%s,%s,%s,%s)
           ON CONFLICT(provider_key,capability,market,symbol,effective_at,payload_sha256) DO UPDATE SET available_at=EXCLUDED.available_at
           RETURNING observation_id""",
        (bar.source, bar.symbol, datetime.combine(bar.trading_date, datetime.min.time(), tzinfo=timezone.utc), as_utc(bar.available_at),
         payload_sha256, Json(normalized), Json(normalized)),
    ).fetchone()
    if amount_mismatch:
        _record_daily_amount_unit_issue(connection, bar)
    existing = connection.execute(
        "SELECT close,selected_provider,source_observation_ids FROM quant.canonical_bars_daily WHERE symbol=%s AND trading_date=%s",
        (bar.symbol, bar.trading_date),
    ).fetchone()
    if existing and existing["close"] and abs(Decimal(existing["close"]) - bar.close) > Decimal("0.001"):
        connection.execute(
            """INSERT INTO quant.data_quality_issues(capability,symbol,trading_date,severity,code,message,details)
               VALUES('daily_bar',%s,%s,'warning','provider_close_conflict','daily close differs across providers',%s)""",
            (bar.symbol, bar.trading_date, Json({"existing_provider": existing["selected_provider"], "existing_close": str(existing["close"]), "incoming_provider": bar.source, "incoming_close": str(bar.close)})),
        )
    replace = existing is None or provider_priority(bar.source) <= provider_priority(str(existing["selected_provider"]))
    selected_provider = bar.source if replace else str(existing["selected_provider"])
    source_ids = ([str(observation["observation_id"])] if not existing else [str(value) for value in (existing["source_observation_ids"] or [])] + [str(observation["observation_id"])])
    if replace:
        connection.execute(
            """INSERT INTO quant.canonical_bars_daily(symbol,trading_date,open,high,low,close,pre_close,volume,amount,adj_factor,is_suspended,limit_up,limit_down,
                 selected_provider,source_observation_ids,quality_status,available_at)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,coalesce(%s,false),%s,%s,%s,%s,%s,%s)
               ON CONFLICT(symbol,trading_date) DO UPDATE SET open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,close=EXCLUDED.close,
                 pre_close=EXCLUDED.pre_close,volume=EXCLUDED.volume,amount=EXCLUDED.amount,
                 adj_factor=coalesce(EXCLUDED.adj_factor,quant.canonical_bars_daily.adj_factor),
                 is_suspended=CASE WHEN %s::boolean IS NULL THEN quant.canonical_bars_daily.is_suspended ELSE EXCLUDED.is_suspended END,
                 limit_up=coalesce(EXCLUDED.limit_up,quant.canonical_bars_daily.limit_up),
                 limit_down=coalesce(EXCLUDED.limit_down,quant.canonical_bars_daily.limit_down),selected_provider=EXCLUDED.selected_provider,
                 source_observation_ids=EXCLUDED.source_observation_ids,quality_status=EXCLUDED.quality_status,available_at=EXCLUDED.available_at,canonicalized_at=now()""",
             (bar.symbol, bar.trading_date, bar.open, bar.high, bar.low, bar.close, bar.pre_close, bar.volume, promoted_amount, bar.adj_factor,
              bar.is_suspended, bar.limit_up, bar.limit_down, selected_provider, source_ids,
              "partial" if amount_mismatch else "fresh", as_utc(bar.available_at), bar.is_suspended),
        )
    else:
        connection.execute("UPDATE quant.canonical_bars_daily SET source_observation_ids=%s,canonicalized_at=now() WHERE symbol=%s AND trading_date=%s", (source_ids, bar.symbol, bar.trading_date))


def _unique_key_rounds(bars: list[DailyBar]) -> list[list[DailyBar]]:
    """Split a batch so no round repeats a (symbol, trading_date).

    The per-bar path reads ``canonical_bars_daily`` back between writes, so a
    repeated key sees its own earlier write.  A batch reads once up front, so a
    repeat inside one round would silently arbitrate against stale state.
    Rounds keep arrival order and are applied in sequence, which reproduces the
    sequential outcome exactly.
    """
    rounds: list[list[DailyBar]] = []
    seen: list[set[tuple[str, date]]] = []
    for bar in bars:
        key = (bar.symbol, bar.trading_date)
        for index, keys in enumerate(seen):
            if key not in keys:
                keys.add(key)
                rounds[index].append(bar)
                break
        else:
            seen.append({key})
            rounds.append([bar])
    return rounds


def _upsert_daily_bar_round(connection: Any, bars: list[DailyBar]) -> None:
    """Apply one round of distinct bars as a handful of batched statements."""
    prepared = []
    for bar in bars:
        mismatch = daily_amount_unit_mismatch(
            source=bar.source, amount=bar.amount, volume=bar.volume, close=bar.close,
        )
        prepared.append((bar, mismatch, None if mismatch else bar.amount))

    ensure_instruments(connection, [InstrumentRecord(
        symbol=bar.symbol, exchange=exchange_for(bar.symbol), name=bar.name,
        industry=bar.industry, is_st=bar.is_st, source=bar.source,
    ) for bar, _mismatch, _amount in prepared], update_existing=True)

    with connection.cursor() as cursor:
        cursor.executemany(
            """INSERT INTO quant.market_bars_daily(symbol,trading_date,open,high,low,close,pre_close,volume,amount,adj_factor,is_suspended,limit_up,limit_down,source,available_at)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,coalesce(%s,false),%s,%s,%s,%s)
               ON CONFLICT(symbol,trading_date) DO UPDATE SET open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,
                 close=EXCLUDED.close,pre_close=EXCLUDED.pre_close,volume=EXCLUDED.volume,amount=EXCLUDED.amount,
                 adj_factor=coalesce(EXCLUDED.adj_factor,quant.market_bars_daily.adj_factor),
                 is_suspended=CASE WHEN %s::boolean IS NULL THEN quant.market_bars_daily.is_suspended ELSE EXCLUDED.is_suspended END,
                 limit_up=coalesce(EXCLUDED.limit_up,quant.market_bars_daily.limit_up),
                 limit_down=coalesce(EXCLUDED.limit_down,quant.market_bars_daily.limit_down),source=EXCLUDED.source,available_at=EXCLUDED.available_at""",
            [(bar.symbol, bar.trading_date, bar.open, bar.high, bar.low, bar.close, bar.pre_close, bar.volume,
              amount, bar.adj_factor, bar.is_suspended, bar.limit_up, bar.limit_down, bar.source,
              as_utc(bar.available_at), bar.is_suspended)
             for bar, _mismatch, amount in prepared],
        )

        observation_rows = []
        for bar, _mismatch, _amount in prepared:
            normalized = bar.model_dump(mode="json")
            observation_rows.append((
                bar.source, bar.symbol,
                datetime.combine(bar.trading_date, datetime.min.time(), tzinfo=timezone.utc),
                as_utc(bar.available_at),
                hashlib.sha256(repr(sorted(normalized.items())).encode("utf-8")).hexdigest(),
                Json(normalized), Json(normalized),
            ))
        cursor.executemany(
            """INSERT INTO quant.raw_market_observations(provider_key,capability,market,symbol,effective_at,available_at,payload_sha256,normalized,payload)
               VALUES(%s,'daily_bar','cn',%s,%s,%s,%s,%s,%s)
               ON CONFLICT(provider_key,capability,market,symbol,effective_at,payload_sha256) DO UPDATE SET available_at=EXCLUDED.available_at
               RETURNING observation_id""",
            observation_rows, returning=True,
        )
        # One result set per parameter row, counted rather than trusted: a
        # driver double whose ``nextset`` never reports exhaustion would
        # otherwise spin forever collecting rows.
        observation_ids: list[Any] = []
        for position in range(len(observation_rows)):
            row = cursor.fetchone()
            observation_ids.append(row["observation_id"] if row else None)
            if position + 1 < len(observation_rows) and not cursor.nextset():
                break
        observation_ids.extend([None] * (len(observation_rows) - len(observation_ids)))

    existing_by_key = {
        (row["symbol"], row["trading_date"]): row
        for row in connection.execute(
            """SELECT symbol,trading_date,close,selected_provider,source_observation_ids
                 FROM quant.canonical_bars_daily
                WHERE (symbol,trading_date) IN (SELECT * FROM unnest(%s::text[],%s::date[]))""",
            ([bar.symbol for bar, _m, _a in prepared], [bar.trading_date for bar, _m, _a in prepared]),
        ).fetchall()
    }

    conflict_issues, replacements, retentions = [], [], []
    for (bar, mismatch, amount), observation_id in zip(prepared, observation_ids):
        if mismatch:
            _record_daily_amount_unit_issue(connection, bar)
        existing = existing_by_key.get((bar.symbol, bar.trading_date))
        if existing and existing["close"] and abs(Decimal(existing["close"]) - bar.close) > Decimal("0.001"):
            conflict_issues.append((bar.symbol, bar.trading_date, Json({
                "existing_provider": existing["selected_provider"], "existing_close": str(existing["close"]),
                "incoming_provider": bar.source, "incoming_close": str(bar.close),
            })))
        replace = existing is None or provider_priority(bar.source) <= provider_priority(str(existing["selected_provider"]))
        selected_provider = bar.source if replace else str(existing["selected_provider"])
        source_ids = ([str(observation_id)] if not existing
                      else [str(value) for value in (existing["source_observation_ids"] or [])] + [str(observation_id)])
        if replace:
            replacements.append((
                bar.symbol, bar.trading_date, bar.open, bar.high, bar.low, bar.close, bar.pre_close, bar.volume,
                amount, bar.adj_factor, bar.is_suspended, bar.limit_up, bar.limit_down, selected_provider,
                source_ids, "partial" if mismatch else "fresh", as_utc(bar.available_at), bar.is_suspended,
            ))
        else:
            retentions.append((source_ids, bar.symbol, bar.trading_date))

    with connection.cursor() as cursor:
        if conflict_issues:
            cursor.executemany(
                """INSERT INTO quant.data_quality_issues(capability,symbol,trading_date,severity,code,message,details)
                   VALUES('daily_bar',%s,%s,'warning','provider_close_conflict','daily close differs across providers',%s)""",
                conflict_issues,
            )
        if replacements:
            cursor.executemany(
                """INSERT INTO quant.canonical_bars_daily(symbol,trading_date,open,high,low,close,pre_close,volume,amount,adj_factor,is_suspended,limit_up,limit_down,
                     selected_provider,source_observation_ids,quality_status,available_at)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,coalesce(%s,false),%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(symbol,trading_date) DO UPDATE SET open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,close=EXCLUDED.close,
                     pre_close=EXCLUDED.pre_close,volume=EXCLUDED.volume,amount=EXCLUDED.amount,
                     adj_factor=coalesce(EXCLUDED.adj_factor,quant.canonical_bars_daily.adj_factor),
                     is_suspended=CASE WHEN %s::boolean IS NULL THEN quant.canonical_bars_daily.is_suspended ELSE EXCLUDED.is_suspended END,
                     limit_up=coalesce(EXCLUDED.limit_up,quant.canonical_bars_daily.limit_up),
                     limit_down=coalesce(EXCLUDED.limit_down,quant.canonical_bars_daily.limit_down),selected_provider=EXCLUDED.selected_provider,
                     source_observation_ids=EXCLUDED.source_observation_ids,quality_status=EXCLUDED.quality_status,available_at=EXCLUDED.available_at,canonicalized_at=now()""",
                replacements,
            )
        if retentions:
            cursor.executemany(
                "UPDATE quant.canonical_bars_daily SET source_observation_ids=%s,canonicalized_at=now() WHERE symbol=%s AND trading_date=%s",
                retentions,
            )


def upsert_daily_bars(connection: Any, bars: list[DailyBar]) -> int:
    """Persist many daily bars with the per-bar contract and far fewer round trips.

    ``upsert_daily_bar`` issues five statements per bar.  The owner database is
    an SSH tunnel away at 52ms per round trip, so a full-market cross-section of
    5,547 bars spent about twenty-four minutes here - the reason the post-close
    sync could never finish inside its persistence budget.

    Every statement, arbitration rule and evidence row is the one the per-bar
    path writes; only the grouping differs.  Front-adjusted rows are rejected
    before anything is written rather than after the bars ahead of them, which
    is the same observable outcome because the caller owns the transaction and
    it is aborted either way.
    """
    if not bars:
        return 0
    for bar in bars:
        if bar.source == "tencent_free":
            raise ValueError("tencent_free front-adjusted daily rows are raw research evidence only")
    for round_bars in _unique_key_rounds(bars):
        _upsert_daily_bar_round(connection, round_bars)
    return len(bars)


__all__ = [
    "TUSHARE_DAILY_AMOUNT_RATIO_MAX", "TUSHARE_DAILY_AMOUNT_RATIO_MIN",
    "TUSHARE_DAILY_AMOUNT_SOURCES", "daily_amount_unit_mismatch", "exchange_for",
    "persisted_adjustment_state", "provider_priority", "quarantine_tushare_daily_amount_mismatches",
    "upsert_daily_bar", "upsert_daily_bars",
]
