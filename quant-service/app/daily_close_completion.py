"""Repair missing settled close rows with explicit, auditable outcomes."""

from __future__ import annotations

import collections
import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any, Callable, Iterable, Mapping

from psycopg.types.json import Json

from .adjustment_factor_semantics import persisted_factor_eligible_sql
from .longhu_market_sync import merge_cross_section
from .longhu_settled_quotes import fetch_classified
from .longhu_market_sync import PROVIDER_KEY

RATIOS = (Decimal("0.05"), Decimal("0.10"), Decimal("0.20"), Decimal("0.30"))
MINIMUM_AGREEMENT = 0.995
FACTOR_PROVIDER = "longhu_qfq_derived"
MAXIMUM_WORK_SET = 600


def _symbol(row: Mapping[str, Any]) -> str:
    return str(row.get("ts_code") or row.get("symbol"))


def _ratio(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _carried_ratio(ratios: Iterable[Any]) -> Decimal | None:
    values = [_ratio(value) for value in ratios]
    if not values:
        return None
    candidate = collections.Counter(values).most_common(1)[0][0]
    return candidate if candidate in RATIOS else None


def _limit_decision(symbol: str, pre_close: Any, ratios: Iterable[Any]) -> tuple[dict[str, Any] | None, str | None]:
    if pre_close is None or Decimal(str(pre_close)) <= 0:
        return None, "no_previous_close"
    ratios = list(ratios)
    ratio = _carried_ratio(ratios)
    if ratio is None:
        return None, "no_earlier_bar_with_limits" if not list(ratios) else "unusual_limit_ratio"
    pre = Decimal(str(pre_close))
    rounding_up = ROUND_FLOOR if symbol.endswith(".BJ") else ROUND_HALF_UP
    rounding_down = ROUND_CEILING if symbol.endswith(".BJ") else ROUND_HALF_UP
    return {
        "ts_code": symbol,
        "up_limit": str((pre * (Decimal("1") + ratio)).quantize(Decimal("0.01"), rounding=rounding_up)),
        "down_limit": str((pre * (Decimal("1") - ratio)).quantize(Decimal("0.01"), rounding=rounding_down)),
        "ratio": str(ratio),
        "method": "gap_completion_limit_carry_v1",
        "ratio_basis": "most_common_ratio_of_last_five_earlier_bars_with_limits",
    }, None


def _limits(symbol: str, pre_close: Any, ratios: Iterable[Any]) -> dict[str, Any] | None:
    return _limit_decision(symbol, pre_close, ratios)[0]


def limit_rows(bars: Iterable[Mapping[str, Any]], trade_date: date | None = None) -> list[dict[str, Any]]:
    """Build carried-limit rows for bars that have a usable recent ratio."""
    result = []
    for bar in bars:
        row = _limits(_symbol(bar), bar.get("pre_close"), bar.get("ratios") or [])
        if row is None:
            continue
        row["trade_date"] = (trade_date or bar.get("trade_date"))
        if isinstance(row["trade_date"], date):
            row["trade_date"] = row["trade_date"].strftime("%Y%m%d")
        result.append(row)
    return result


def limit_rule_agreement(bars: Iterable[Mapping[str, Any]]) -> float:
    checked = agreed = 0
    for bar in bars:
        if bar.get("limit_up") is None or bar.get("limit_down") is None:
            continue
        computed = _limits(_symbol(bar), bar.get("pre_close"), bar.get("ratios") or [])
        if computed is None:
            continue
        checked += 1
        agreed += (Decimal(str(computed["up_limit"])) == Decimal(str(bar["limit_up"])) and
                   Decimal(str(computed["down_limit"])) == Decimal(str(bar["limit_down"])))
    return agreed / checked if checked else 0.0


def factor_rows(candidates: Iterable[Mapping[str, Any]], trade_date: date | None = None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    pending: dict[str, str] = {}
    for candidate in candidates:
        symbol = _symbol(candidate)
        previous = candidate.get("previous") or candidate.get("prev_factor")
        following = candidate.get("following") or candidate.get("next_factor")
        if previous is None:
            pending[symbol] = "no_earlier_factor"
            continue
        previous_value = previous.get("adj_factor") if isinstance(previous, Mapping) else previous
        previous_date = previous.get("trading_date") if isinstance(previous, Mapping) else candidate.get("prev_date")
        following_value = following.get("adj_factor") if isinstance(following, Mapping) else following
        following_date = following.get("trading_date") if isinstance(following, Mapping) else candidate.get("next_date")
        if following_value is not None and Decimal(str(following_value)) != Decimal(str(previous_value)):
            pending[symbol] = "factor_changes_across_session"
            continue
        if candidate.get("ex_rights_on_day"):
            pending[symbol] = "ex_rights_on_session"
            continue
        day = trade_date or candidate.get("trade_date")
        rows.append({
            "ts_code": symbol, "trade_date": day.strftime("%Y%m%d") if isinstance(day, date) else day,
            "adj_factor": str(previous_value), "step": 1.0,
            "method": "gap_completion_carry_v1",
            "anchor": {"date": str(previous_date), "kind": "stored_factor",
                       "provider": FACTOR_PROVIDER, "adj_factor": str(previous_value)},
            "basis": "equal_neighbouring_factors" if following_value is not None
                     else "no_ex_rights_marker_on_latest_session",
            "next": None if following_value is None else {"date": str(following_date), "adj_factor": str(following_value)},
        })
    return {"rows": rows, "pending": pending}


def completion_status(outcomes: Mapping[str, Iterable[Any]], *, limits_pending: Iterable[Any] = (),
                      factor_pending: Iterable[Any] = (), other_pending: Iterable[Any] = ()) -> str:
    leftovers = any(outcomes.get(name) for name in ("adjusted_later", "unverified", "invalid", "failed"))
    return "partial" if leftovers or list(limits_pending) or list(factor_pending) or list(other_pending) else "completed"


def _read_state(db: Any, day: date) -> dict[str, Any]:
  with db.transaction() as connection:
    roster = [str(row["symbol"]) for row in connection.execute(
        "SELECT symbol FROM quant.universe_members WHERE universe_key='all_a' AND enabled ORDER BY symbol").fetchall()]
    rows = connection.execute(
        """SELECT b.symbol,b.trading_date,b.open,b.high,b.low,b.close,b.pre_close,
                         b.limit_up,b.limit_down,b.adj_factor,b.is_suspended,b.quality_status
             FROM quant.canonical_bars_daily b
             JOIN quant.universe_members u ON u.universe_key='all_a' AND u.enabled AND u.symbol=b.symbol
            WHERE b.trading_date=%s""", (day,)).fetchall()
    return {"roster": roster, "bars": [dict(row) for row in rows]}


def _day_bars(connection: Any, day: date, symbols: Iterable[str] | None = None) -> list[dict[str, Any]]:
    if symbols is None:
        rows = connection.execute(
            """SELECT b.symbol,b.open,b.high,b.low,b.close,b.pre_close,b.limit_up,b.limit_down,
                              b.adj_factor,b.is_suspended,b.quality_status
                         FROM quant.canonical_bars_daily b
                         JOIN quant.universe_members u ON u.universe_key='all_a' AND u.enabled AND u.symbol=b.symbol
                        WHERE b.trading_date=%s""", (day,)).fetchall()
    else:
        rows = connection.execute(
            """SELECT b.symbol,b.open,b.high,b.low,b.close,b.pre_close,b.limit_up,b.limit_down,
                              b.adj_factor,b.is_suspended,b.quality_status
                         FROM quant.canonical_bars_daily b
                        WHERE b.trading_date=%s AND b.symbol=ANY(%s)""", (day, list(symbols))).fetchall()
    return [dict(row) for row in rows]


def _day_ratios(connection: Any, day: date) -> dict[str, list[Any]]:
    rows = connection.execute(
        """SELECT u.symbol,coalesce(array_agg(round(p.limit_up / p.pre_close - 1, 2)
                                      ORDER BY p.trading_date DESC),'{}') AS ratios
             FROM quant.universe_members u
             LEFT JOIN LATERAL (
               SELECT p.trading_date,p.limit_up,p.pre_close
                 FROM quant.canonical_bars_daily p
                WHERE p.symbol=u.symbol AND p.trading_date<%s
                  AND p.trading_date>=%s-40 AND p.limit_up>0 AND p.pre_close>0
                ORDER BY p.trading_date DESC LIMIT 5) p ON true
            WHERE u.universe_key='all_a' AND u.enabled
            GROUP BY u.symbol""", (day, day)).fetchall()
    return {str(row["symbol"]): list(row["ratios"] or []) for row in rows}


def _factor_state(connection: Any, day: date, symbols: Iterable[str]) -> list[dict[str, Any]]:
    rows = connection.execute(
        f"""SELECT u.symbol,prev.trading_date AS prev_date,prev.adj_factor AS prev_factor,
                         nxt.trading_date AS next_date,nxt.adj_factor AS next_factor
                    FROM quant.universe_members u
                    LEFT JOIN LATERAL (
                      SELECT f.trading_date,f.adj_factor FROM quant.daily_adjustment_factors f
                       WHERE f.symbol=u.symbol AND f.trading_date<%s AND f.provider=%s
                         AND {persisted_factor_eligible_sql('f')}
                       ORDER BY f.trading_date DESC LIMIT 1) prev ON true
                    LEFT JOIN LATERAL (
                      SELECT f.trading_date,f.adj_factor FROM quant.daily_adjustment_factors f
                       WHERE f.symbol=u.symbol AND f.trading_date>%s AND f.provider=%s
                         AND {persisted_factor_eligible_sql('f')}
                       ORDER BY f.trading_date LIMIT 1) nxt ON true
                   WHERE u.universe_key='all_a' AND u.enabled AND u.symbol=ANY(%s)""",
        (day, FACTOR_PROVIDER, day, FACTOR_PROVIDER, list(symbols))).fetchall()
    return [dict(row) for row in rows]


def _persist_receipt(connection: Any, key: str, trade_date: date, status: str,
                     metadata: Mapping[str, Any], observed_at: datetime) -> None:
    connection.execute(
        """INSERT INTO quant.fetch_runs(provider_key,capability,trade_date,request_key,status,attempt_count,
                                          started_at,finished_at,row_count,metadata)
           VALUES(%s,'daily_close_completion',%s,%s,%s,1,%s,%s,%s,%s)
           ON CONFLICT(request_key) DO UPDATE SET status=EXCLUDED.status,finished_at=EXCLUDED.finished_at,
             row_count=EXCLUDED.row_count,metadata=EXCLUDED.metadata""",
        (PROVIDER_KEY, trade_date, key, status, observed_at, observed_at,
         int(metadata.get("written", 0)), Json(dict(metadata))),
    )


def _write_receipt(database: Any, key: str, trade_date: date, status: str, metadata: Mapping[str, Any],
                   observed_at: datetime) -> dict[str, Any]:
    with database.transaction() as connection:
        _persist_receipt(connection, key, trade_date, status, metadata, observed_at)
    return {"written": int(metadata.get("written", 0))}


def _error_detail(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"[:500]


async def complete(trade_date: date, *, db: Any, run_public_blocking: Callable[..., Any],
                   run_database_blocking: Callable[..., Any], persist_rows: Callable[..., int],
                   read_source_factory: Callable[[], Any], now: datetime | None = None) -> dict[str, Any]:
    observed_at = now or datetime.now(timezone.utc)
    state = await run_database_blocking(_read_state, db, trade_date, timeout_seconds=30)
    bars_by_symbol = {row["symbol"]: row for row in state["bars"]}
    roster = set(state["roster"])
    missing = sorted(roster - set(bars_by_symbol))
    bars_without_limits = sorted(symbol for symbol, row in bars_by_symbol.items()
                                 if row.get("limit_up") is None or row.get("limit_down") is None)
    bars_without_factor = sorted(symbol for symbol, row in bars_by_symbol.items()
                                 if row.get("adj_factor") is None or Decimal(str(row["adj_factor"])) <= 0)
    unusable_existing = sorted(symbol for symbol, row in bars_by_symbol.items()
                               if not row.get("is_suspended") and
                               (any(row.get(field) is None for field in ("open", "high", "low", "close")) or
                                row.get("quality_status") not in ("fresh", "partial")))
    request_symbols = sorted(set(missing) | set(bars_without_factor))
    key = hashlib.sha256(json.dumps({"capability": "daily_close_completion", "trade_date": str(trade_date),
                                     "symbols": request_symbols, "observed_at": observed_at.isoformat()},
                                    sort_keys=True).encode()).hexdigest()

    def finish(status: str, **metadata: Any) -> dict[str, Any]:
        receipt = {"trade_date": str(trade_date), "status": status, "missing": missing,
                   "unusable_existing": unusable_existing, **metadata}
        receipt.update({"request_key": key, "written": int(receipt.get("written", 0))})
        return receipt

    if len(request_symbols) > MAXIMUM_WORK_SET:
        result = finish("blocked", reason="close_or_controls_not_landed",
                        missing_bar_count=len(missing), missing_factor_count=len(bars_without_factor))
        await run_database_blocking(
            _write_receipt, db, key, trade_date, "blocked", result, observed_at, timeout_seconds=30,
        )
        return result

    try:
        source_owner = read_source_factory()
        source = source_owner if hasattr(source_owner, "raw_call") else source_owner.quote_source()
    except Exception as error:
        result = finish("blocked", reason=_error_detail(error))
        await run_database_blocking(_write_receipt, db, key, trade_date, "blocked", result, observed_at, timeout_seconds=30)
        return result

    empty_outcomes = {name: [] for name in ("filled", "not_traded", "adjusted_later", "unverified", "invalid", "failed")}
    if request_symbols:
        try:
            quotes, health = await run_public_blocking(
                fetch_classified, source, request_symbols, trade_date, workers=8,
                deadline_seconds=240, timeout_seconds=300,
            )
        except Exception as error:
            result = finish("failed", reason=_error_detail(error), missing_bar_count=len(missing),
                            missing_factor_count=len(bars_without_factor))
            await run_database_blocking(
                _write_receipt, db, key, trade_date, "failed", result, observed_at, timeout_seconds=30,
            )
            return result
    else:
        quotes, health = [], {"outcomes": empty_outcomes, "errors": {}, "ex_rights_on_day": {}}
    outcomes = health["outcomes"]
    filled = {row["ts_code"] for row in quotes}

    def persist(database: Any) -> dict[str, Any]:
      with database.transaction() as connection:
        merged = merge_cross_section(trade_date, {}, quotes)
        current = {row["symbol"]: row for row in _day_bars(connection, trade_date)}
        daily_rows = [row for row in merged.daily_rows if row["ts_code"] not in current]
        # The re-read and normalizer upsert are not atomic. The close is not writing at this time,
        # and both use the same vendor series, which matched all seven fields on 150 repaired stocks.
        written = persist_rows(connection, "daily", key + ":daily", daily_rows, PROVIDER_KEY, observed_at)
        current = {row["symbol"]: row for row in _day_bars(connection, trade_date)}
        stored_filled = filled & set(current)
        bar_rejected = sorted(filled - stored_filled)
        ratios = _day_ratios(connection, trade_date)
        for row in current.values():
            row["ratios"] = ratios.get(row["symbol"], [])
        agreement = limit_rule_agreement(current.values())
        eligible_limit_symbols = set(bars_without_limits) | stored_filled
        missing_limit_rows = [row for symbol, row in current.items()
                              if symbol in eligible_limit_symbols and
                              (row.get("limit_up") is None or row.get("limit_down") is None)]
        limit_pending: dict[str, str] = {}
        carried = []
        for row in missing_limit_rows:
            candidate, reason = _limit_decision(row["symbol"], row.get("pre_close"), row["ratios"])
            if candidate is None:
                limit_pending[row["symbol"]] = reason or "unusual_limit_ratio"
            else:
                carried.append({**candidate, "trade_date": trade_date.strftime("%Y%m%d")})
        if agreement < MINIMUM_AGREEMENT:
            limit_pending.update({row["symbol"]: "agreement_below_0.995" for row in missing_limit_rows
                                  if row["symbol"] not in limit_pending})
            carried = []
        if carried:
            written += persist_rows(connection, "stk_limit", key + ":stk_limit", carried, PROVIDER_KEY, observed_at)
        current = {row["symbol"]: row for row in _day_bars(connection, trade_date)}
        factor_candidates = [
            {**row, "ex_rights_on_day": health["ex_rights_on_day"].get(row["symbol"], False)}
            for row in _factor_state(connection, trade_date, stored_filled)
            if row["symbol"] in stored_filled and
            (current[row["symbol"]].get("adj_factor") is None or
             Decimal(str(current[row["symbol"]]["adj_factor"])) <= 0)
        ]
        factors = factor_rows(factor_candidates, trade_date)
        written += persist_rows(connection, "adj_factor", key + ":adj_factor", factors["rows"], FACTOR_PROVIDER, observed_at)
        factor_pending = factors["pending"]
        metadata = {"outcomes": outcomes, "errors": health.get("errors", {}), "agreement": agreement,
                    "limits_pending": limit_pending, "factor_pending": factor_pending,
                    "missing": missing, "bar_rejected": bar_rejected,
                    "unusable_existing": unusable_existing, "written": written}
        status = completion_status(
            outcomes, limits_pending=limit_pending, factor_pending=factor_pending,
            other_pending=bar_rejected + unusable_existing,
        )
        _persist_receipt(connection, key, trade_date, status, metadata, observed_at)
        return {"status": status, **metadata}

    try:
        persisted = await run_database_blocking(persist, db, timeout_seconds=120)
    except Exception as error:
        result = finish("failed", reason=_error_detail(error), missing_bar_count=len(missing),
                        missing_factor_count=len(bars_without_factor))
        await run_database_blocking(
            _write_receipt, db, key, trade_date, "failed", result, observed_at, timeout_seconds=30,
        )
        return result
    return {"trade_date": str(trade_date), "request_key": key, **persisted}


__all__ = ["MINIMUM_AGREEMENT", "completion_status", "complete", "factor_rows",
           "limit_rows", "limit_rule_agreement"]
