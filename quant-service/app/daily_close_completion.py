"""Repair missing settled close rows with explicit, auditable outcomes."""

from __future__ import annotations

import collections
import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any, Callable, Iterable, Mapping

from .adjustment_factor_semantics import persisted_factor_eligible_sql
from .longhu_market_sync import merge_cross_section
from .longhu_settled_quotes import fetch_classified
from .longhu_market_sync import PROVIDER_KEY

RATIOS = (Decimal("0.05"), Decimal("0.10"), Decimal("0.20"), Decimal("0.30"))
MINIMUM_AGREEMENT = 0.995
FACTOR_PROVIDER = "longhu_qfq_derived"


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


def _limits(symbol: str, pre_close: Any, ratios: Iterable[Any]) -> dict[str, Any] | None:
    ratio = _carried_ratio(ratios)
    if ratio is None:
        return None
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
    }


def limit_rows(bars: Iterable[Mapping[str, Any]], trade_date: date | None = None) -> list[dict[str, Any]]:
    """Build carried-limit rows for bars that have a usable recent ratio."""
    result = []
    for bar in bars:
        row = _limits(_symbol(bar), bar["pre_close"], bar.get("ratios") or [])
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
        computed = _limits(_symbol(bar), bar["pre_close"], bar.get("ratios") or [])
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
                      factor_pending: Iterable[Any] = ()) -> str:
    leftovers = any(outcomes.get(name) for name in ("adjusted_later", "unverified", "invalid", "failed"))
    return "partial" if leftovers or list(limits_pending) or list(factor_pending) else "completed"


def _read_state(db: Any, day: date) -> dict[str, Any]:
  with db.transaction() as connection:
    roster = [str(row["symbol"]) for row in connection.execute(
        "SELECT symbol FROM quant.universe_members WHERE universe_key='all_a' AND enabled ORDER BY symbol").fetchall()]
    rows = connection.execute(
        """SELECT b.symbol,b.trading_date,b.pre_close,b.limit_up,b.limit_down,
                  prev.ratios
             FROM quant.canonical_bars_daily b
             JOIN quant.universe_members u ON u.universe_key='all_a' AND u.enabled AND u.symbol=b.symbol
             LEFT JOIN LATERAL (
               SELECT array_agg(round(p.limit_up / p.pre_close - 1, 2) ORDER BY p.trading_date DESC) AS ratios
                 FROM (SELECT p.trading_date,p.limit_up,p.pre_close
                         FROM quant.canonical_bars_daily p
                        WHERE p.symbol=b.symbol AND p.trading_date<b.trading_date
                          AND p.trading_date>=b.trading_date-40 AND p.limit_up>0 AND p.pre_close>0
                        ORDER BY p.trading_date DESC LIMIT 5) p
             ) prev ON true
            WHERE b.trading_date=%s""", (day,)).fetchall()
    factors = connection.execute(
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
                   WHERE u.universe_key='all_a' AND u.enabled""",
        (day, FACTOR_PROVIDER, day, FACTOR_PROVIDER)).fetchall()
    return {"roster": roster, "bars": [dict(row) for row in rows], "factors": [dict(row) for row in factors]}


async def complete(trade_date: date, *, db: Any, run_public_blocking: Callable[..., Any],
                   run_database_blocking: Callable[..., Any], persist_rows: Callable[..., int],
                   read_source_factory: Callable[[], Any], now: datetime | None = None) -> dict[str, Any]:
    observed_at = now or datetime.now(timezone.utc)
    try:
        source_owner = read_source_factory()
        source = getattr(source_owner, "quote_source", source_owner)
    except Exception as error:
        return {"status": "blocked", "trade_date": str(trade_date), "reason": f"{type(error).__name__}: {error}"}
    state = await run_database_blocking(_read_state, db, trade_date, timeout_seconds=30)
    present = {row["symbol"] for row in state["bars"]}
    missing = [symbol for symbol in state["roster"] if symbol not in present]
    if not missing:
        return {"status": "completed", "trade_date": str(trade_date), "missing": [], "written": 0}
    quotes, health = await run_public_blocking(fetch_classified, source, missing, trade_date,
                                               workers=6, timeout_seconds=120)
    outcomes = health["outcomes"]
    filled = {row["ts_code"] for row in quotes}
    bars = [row for row in state["bars"] if row["symbol"] in present]
    fetched = merge_cross_section(trade_date, {}, quotes).daily_rows
    candidate_bars = bars + [{**row, "symbol": row["ts_code"]} for row in fetched]
    agreement = limit_rule_agreement(candidate_bars)
    carried = limit_rows([row for row in candidate_bars if row["symbol"] in filled], trade_date)
    limits_pending = [] if agreement >= MINIMUM_AGREEMENT else sorted(filled)
    factors = factor_rows([
        {**row, "ex_rights_on_day": health["ex_rights_on_day"].get(row["symbol"], False)}
        for row in state["factors"] if row["symbol"] in filled
    ], trade_date)
    key = hashlib.sha256(json.dumps({"capability": "daily_close_completion", "trade_date": str(trade_date),
                                     "symbols": sorted(filled)}, sort_keys=True).encode()).hexdigest()

    def persist(database: Any) -> dict[str, Any]:
      with database.transaction() as connection:
        merged = merge_cross_section(trade_date, {}, quotes)
        written = persist_rows(connection, "daily", key + ":daily", merged.daily_rows, PROVIDER_KEY, observed_at)
        if not limits_pending:
            written += persist_rows(connection, "stk_limit", key + ":stk_limit", carried, PROVIDER_KEY, observed_at)
        written += persist_rows(connection, "adj_factor", key + ":adj_factor", factors["rows"], FACTOR_PROVIDER, observed_at)
        metadata = {"outcomes": outcomes, "agreement": agreement, "limits_pending": limits_pending,
                    "factor_pending": factors["pending"], "missing": missing}
        connection.execute(
            """INSERT INTO quant.fetch_runs(provider_key,capability,trade_date,request_key,status,attempt_count,
                                              started_at,finished_at,row_count,metadata)
               VALUES(%s,'daily_close_completion',%s,%s,%s,1,%s,%s,%s,%s)
               ON CONFLICT(request_key) DO UPDATE SET status=EXCLUDED.status,finished_at=EXCLUDED.finished_at,
                 row_count=EXCLUDED.row_count,metadata=EXCLUDED.metadata""",
            (PROVIDER_KEY, trade_date, key,
             completion_status(outcomes, limits_pending=limits_pending, factor_pending=factors["pending"]),
             observed_at, observed_at, written, json.dumps(metadata, default=str)),
        )
        return {"written": written}

    persisted = await run_database_blocking(persist, db, timeout_seconds=120)
    return {"status": completion_status(outcomes, limits_pending=limits_pending,
                                         factor_pending=factors["pending"]),
            "trade_date": str(trade_date), "missing": missing, "outcomes": outcomes,
            "agreement": agreement, "limits_pending": limits_pending,
            "factor_pending": factors["pending"], **persisted}


__all__ = ["MINIMUM_AGREEMENT", "completion_status", "complete", "factor_rows",
           "limit_rows", "limit_rule_agreement"]
