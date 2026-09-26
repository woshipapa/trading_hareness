"""Normalize every post-close/board-mining candidate line into one comparable ledger.

Before this, ``post_close_strategy_candidates``, ``strategy_pattern_samples``,
``ten_day_leader_rotation_candidates``, ``intraday_limit_linkage_candidates``,
``intraday_board_stock_mining_candidates`` and ``recommendations`` each scored
candidates on their own scale in their own table, and nothing ever asked
"what were today's best ideas across every strategy". This module reads each
source's already-persisted output (it never recomputes a score) and
materializes one row per (strategy_key, as_of_date, symbol) into
``quant.strategy_daily_candidates``, tagged with a liquidity screen and the
native score's scale so a comparison never silently treats a 0-100 bounded
score and an unbounded percent return as the same number.

``strategy_key`` values this module writes: post_close_base_ready,
post_close_base_forming, post_close_fresh_start, post_close_limit_pattern,
ten_day_leader_rotation, limit_linkage, board_stock_mining_inflow,
board_stock_mining_outflow, daily_recommendation.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from psycopg.types.json import Json

from .liquidity_screen import liquidity_eligibility, median_daily_amount_by_symbol
from .point_in_time import exchange_day_end
from .point_in_time_status import st_flags_as_of
from .t1_settlement import SETTLEMENT_VERSION
from .t1_settlement_repository import (
    EqualWeightMarket, settle_idea, settlement_columns, settlement_update_sql, settlement_values,
)

POST_CLOSE_STRATEGY_KEYS = {
    "base_ready_30d": "post_close_base_ready",
    "base_forming_15d": "post_close_base_forming",
    "fresh_start_15d": "post_close_fresh_start",
}


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _liquidity_context(connection: Any, symbols: list[str], as_of_date: date) -> dict[str, dict[str, Any]]:
    """Batch-fetch everything liquidity_eligibility() needs, once per materialize call."""
    if not symbols:
        return {}
    median_amount = median_daily_amount_by_symbol(connection, symbols, as_of_date)
    instrument_rows = connection.execute(
        "SELECT symbol,list_date FROM quant.instruments WHERE symbol=ANY(%s)", (symbols,),
    ).fetchall()
    instruments = {str(row["symbol"]): dict(row) for row in instrument_rows}
    # A re-materialized past date must see that session's ST status.
    st_status = st_flags_as_of(connection, symbols, as_of_date)
    latest_bar_rows = connection.execute(
        """SELECT DISTINCT ON (symbol) symbol,close,is_suspended FROM quant.canonical_bars_daily
             WHERE symbol=ANY(%s) AND trading_date<=%s AND available_at<=%s AND quality_status='fresh'
             ORDER BY symbol,trading_date DESC""",
        (symbols, as_of_date, exchange_day_end(as_of_date)),
    ).fetchall()
    latest_bars = {str(row["symbol"]): dict(row) for row in latest_bar_rows}
    context: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        instrument = instruments.get(symbol) or {}
        bar = latest_bars.get(symbol) or {}
        eligible, flags = liquidity_eligibility(
            median_daily_amount=median_amount.get(symbol), latest_price=_number(bar.get("close")),
            list_date=instrument.get("list_date"), as_of_date=as_of_date,
            is_st=bool((st_status.get(symbol) or {}).get("is_st")), is_suspended=bool(bar.get("is_suspended")),
        )
        context[symbol] = {"eligible": eligible, "flags": flags}
    return context


def _upsert_candidates(connection: Any, rows: list[dict[str, Any]]) -> int:
    """rows: strategy_key, as_of_date, symbol, source_table, source_run_id, rank, raw_score, score_scale, evidence."""
    stored = 0
    for row in rows:
        connection.execute(
            """INSERT INTO quant.strategy_daily_candidates(
                    strategy_key,as_of_date,symbol,source_table,source_run_id,rank,raw_score,score_scale,
                    liquidity_eligible,liquidity_flags,evidence)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(strategy_key,as_of_date,symbol) DO UPDATE SET
                 source_run_id=EXCLUDED.source_run_id,rank=EXCLUDED.rank,raw_score=EXCLUDED.raw_score,
                 score_scale=EXCLUDED.score_scale,liquidity_eligible=EXCLUDED.liquidity_eligible,
                 liquidity_flags=EXCLUDED.liquidity_flags,evidence=EXCLUDED.evidence,materialized_at=now()""",
            (row["strategy_key"], row["as_of_date"], row["symbol"], row["source_table"], row.get("source_run_id"),
             row.get("rank"), row.get("raw_score"), row["score_scale"], row["liquidity"]["eligible"],
             Json(row["liquidity"]["flags"]), Json(row.get("evidence") or {})),
        )
        stored += 1
    return stored


def materialize_post_close_candidates(connection: Any, as_of_date: date) -> int:
    candidates = connection.execute(
        """SELECT c.symbol,c.rank,c.score,c.candidate_type,c.run_id
             FROM quant.post_close_strategy_candidates c JOIN quant.post_close_strategy_runs r ON r.run_id=c.run_id
            WHERE r.as_of_date=%s""",
        (as_of_date,),
    ).fetchall()
    liquidity = _liquidity_context(connection, [str(row["symbol"]) for row in candidates], as_of_date)
    rows = [{
        "strategy_key": POST_CLOSE_STRATEGY_KEYS[row["candidate_type"]], "as_of_date": as_of_date,
        "symbol": row["symbol"], "source_table": "post_close_strategy_candidates", "source_run_id": row["run_id"],
        "rank": row["rank"], "raw_score": row["score"], "score_scale": "bounded_0_100",
        "liquidity": liquidity.get(str(row["symbol"]), {"eligible": False, "flags": ["liquidity_context_missing"]}),
    } for row in candidates]
    return _upsert_candidates(connection, rows)


def materialize_pattern_candidates(connection: Any, as_of_date: date) -> int:
    candidates = connection.execute(
        """SELECT s.symbol,s.rank,s.run_id,(s.limit_context->>'review_score')::numeric review_score
             FROM quant.strategy_pattern_samples s JOIN quant.strategy_pattern_runs r ON r.run_id=s.run_id
            WHERE r.as_of_date=%s AND s.limit_context->>'review_score' IS NOT NULL""",
        (as_of_date,),
    ).fetchall()
    liquidity = _liquidity_context(connection, [str(row["symbol"]) for row in candidates], as_of_date)
    rows = [{
        "strategy_key": "post_close_limit_pattern", "as_of_date": as_of_date, "symbol": row["symbol"],
        "source_table": "strategy_pattern_samples", "source_run_id": row["run_id"], "rank": row["rank"],
        "raw_score": row["review_score"], "score_scale": "bounded_0_100",
        "liquidity": liquidity.get(str(row["symbol"]), {"eligible": False, "flags": ["liquidity_context_missing"]}),
    } for row in candidates]
    return _upsert_candidates(connection, rows)


def materialize_ten_day_leader_candidates(connection: Any, as_of_date: date) -> int:
    candidates = connection.execute(
        """SELECT c.symbol,c.board_rank,c.run_id,c.ten_day_return_pct
             FROM quant.ten_day_leader_rotation_candidates c JOIN quant.ten_day_leader_rotation_runs r ON r.run_id=c.run_id
            WHERE r.as_of_date=%s""",
        (as_of_date,),
    ).fetchall()
    liquidity = _liquidity_context(connection, [str(row["symbol"]) for row in candidates], as_of_date)
    rows = [{
        "strategy_key": "ten_day_leader_rotation", "as_of_date": as_of_date, "symbol": row["symbol"],
        "source_table": "ten_day_leader_rotation_candidates", "source_run_id": row["run_id"], "rank": row["board_rank"],
        "raw_score": row["ten_day_return_pct"], "score_scale": "percent_return_unbounded",
        "liquidity": liquidity.get(str(row["symbol"]), {"eligible": False, "flags": ["liquidity_context_missing"]}),
    } for row in candidates]
    return _upsert_candidates(connection, rows)


def materialize_limit_linkage_candidates(connection: Any, as_of_date: date) -> int:
    run = connection.execute(
        """SELECT linkage_run_id FROM quant.intraday_limit_linkage_mining_runs
             WHERE trade_date=%s ORDER BY observed_at DESC LIMIT 1""",
        (as_of_date,),
    ).fetchone()
    if run is None:
        return 0
    candidates = connection.execute(
        "SELECT symbol,rank,score FROM quant.intraday_limit_linkage_candidates WHERE linkage_run_id=%s",
        (run["linkage_run_id"],),
    ).fetchall()
    liquidity = _liquidity_context(connection, [str(row["symbol"]) for row in candidates], as_of_date)
    rows = [{
        "strategy_key": "limit_linkage", "as_of_date": as_of_date, "symbol": row["symbol"],
        "source_table": "intraday_limit_linkage_candidates", "source_run_id": run["linkage_run_id"],
        "rank": row["rank"], "raw_score": row["score"], "score_scale": "unbounded_positive",
        "liquidity": liquidity.get(str(row["symbol"]), {"eligible": False, "flags": ["liquidity_context_missing"]}),
    } for row in candidates]
    return _upsert_candidates(connection, rows)


def materialize_board_stock_mining_candidates(connection: Any, as_of_date: date) -> int:
    run = connection.execute(
        """SELECT mining_run_id FROM quant.intraday_board_stock_mining_runs
             WHERE (observed_at AT TIME ZONE 'Asia/Shanghai')::date=%s ORDER BY observed_at DESC LIMIT 1""",
        (as_of_date,),
    ).fetchone()
    if run is None:
        return 0
    candidates = connection.execute(
        "SELECT symbol,rank,score,direction FROM quant.intraday_board_stock_mining_candidates WHERE mining_run_id=%s",
        (run["mining_run_id"],),
    ).fetchall()
    liquidity = _liquidity_context(connection, [str(row["symbol"]) for row in candidates], as_of_date)
    rows = [{
        "strategy_key": f"board_stock_mining_{row['direction']}", "as_of_date": as_of_date, "symbol": row["symbol"],
        "source_table": "intraday_board_stock_mining_candidates", "source_run_id": run["mining_run_id"],
        "rank": row["rank"], "raw_score": row["score"], "score_scale": "unbounded_positive",
        "liquidity": liquidity.get(str(row["symbol"]), {"eligible": False, "flags": ["liquidity_context_missing"]}),
    } for row in candidates]
    return _upsert_candidates(connection, rows)


def materialize_recommendation_candidates(connection: Any, as_of_date: date) -> int:
    candidates = connection.execute(
        """SELECT x.symbol,x.rank,x.score,x.run_id,x.direction
             FROM quant.recommendations x JOIN quant.recommendation_runs r ON r.run_id=x.run_id
            WHERE r.as_of_date=%s AND x.direction<>0""",
        (as_of_date,),
    ).fetchall()
    liquidity = _liquidity_context(connection, [str(row["symbol"]) for row in candidates], as_of_date)
    rows = [{
        "strategy_key": "daily_recommendation", "as_of_date": as_of_date, "symbol": row["symbol"],
        "source_table": "recommendations", "source_run_id": row["run_id"], "rank": row["rank"],
        "raw_score": row["score"], "score_scale": "bounded_0_100",
        "liquidity": liquidity.get(str(row["symbol"]), {"eligible": False, "flags": ["liquidity_context_missing"]}),
        "evidence": {"direction": row["direction"]},
    } for row in candidates]
    return _upsert_candidates(connection, rows)


MATERIALIZERS = (
    materialize_post_close_candidates, materialize_pattern_candidates, materialize_ten_day_leader_candidates,
    materialize_limit_linkage_candidates, materialize_board_stock_mining_candidates, materialize_recommendation_candidates,
)


def materialize_ledger(connection: Any, as_of_date: date) -> dict[str, int]:
    return {materializer.__name__: materializer(connection, as_of_date) for materializer in MATERIALIZERS}


HORIZON_DAYS = 10


def candidate_direction(strategy_key: str, evidence: Any) -> int:
    """The side a ledger candidate's own hypothesis takes.

    Board-mining outflow picks name the members leading a sector *down*;
    settling them as longs inverted the hypothesis they test.
    """
    if str(strategy_key).endswith("_outflow"):
        return -1
    if isinstance(evidence, dict) and _number(evidence.get("direction")) is not None:
        return -1 if float(_number(evidence.get("direction"))) < 0 else 1
    return 1


def settle_ledger_outcomes(connection: Any, as_of_date: date) -> int:
    """Settle every ledger candidate not yet settled under the current T+1 rule."""
    candidates = [dict(row) for row in connection.execute(
        """SELECT c.strategy_key,c.as_of_date candidate_date,c.symbol,c.evidence
             FROM quant.strategy_daily_candidates c
            WHERE c.as_of_date<%s
              AND NOT EXISTS (
                  SELECT 1 FROM quant.strategy_daily_candidate_outcomes settled
                   WHERE settled.strategy_key=c.strategy_key AND settled.as_of_date=c.as_of_date
                     AND settled.symbol=c.symbol AND settled.settlement_version=%s)""",
        (as_of_date, SETTLEMENT_VERSION),
    ).fetchall()]
    if not candidates:
        return 0
    market = EqualWeightMarket(connection, min(row["candidate_date"] for row in candidates), as_of_date)
    columns = ["strategy_key", "as_of_date", "symbol", "horizon_days", *settlement_columns("entry_price", "exit_price")]
    settled = 0
    for row in candidates:
        result = settle_idea(
            connection, symbol=row["symbol"], signal_date=row["candidate_date"], as_of_date=as_of_date,
            direction=candidate_direction(row["strategy_key"], row.get("evidence")), horizon_sessions=HORIZON_DAYS,
            market=market,
        )
        if result["status"] != "settled":
            continue
        connection.execute(
            f"""INSERT INTO quant.strategy_daily_candidate_outcomes({",".join(columns)})
                VALUES({",".join(["%s"] * len(columns))})
                ON CONFLICT(strategy_key,as_of_date,symbol) DO UPDATE SET horizon_days=EXCLUDED.horizon_days,
                  {settlement_update_sql("entry_price", "exit_price", keys=("strategy_key", "as_of_date", "symbol"))}""",
            (row["strategy_key"], row["candidate_date"], row["symbol"], HORIZON_DAYS, *settlement_values(result)),
        )
        settled += 1
    return settled


__all__ = [
    "HORIZON_DAYS", "MATERIALIZERS", "POST_CLOSE_STRATEGY_KEYS",
    "materialize_board_stock_mining_candidates", "materialize_ledger", "materialize_limit_linkage_candidates",
    "materialize_pattern_candidates", "materialize_post_close_candidates", "materialize_recommendation_candidates",
    "materialize_ten_day_leader_candidates", "candidate_direction", "settle_ledger_outcomes",
]
