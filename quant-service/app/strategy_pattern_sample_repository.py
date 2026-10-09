"""Bounded persisted inputs for post-close limit-pattern sample selection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from psycopg.types.json import Json

from .limit_event_fallback import enrich_pool_records
from .limit_event_repository import load_close_limit_events, load_prior_close_pool


@dataclass(frozen=True)
class StrategyPatternSampleInputs:
    limit_rows: list[dict[str, Any]]
    step_rows: list[dict[str, Any]]
    prior_limit_rows: list[dict[str, Any]]
    control_rows: list[dict[str, Any]]
    daily_rows: list[dict[str, Any]]


def load_strategy_pattern_sample_inputs(database: Any, as_of_date: date) -> StrategyPatternSampleInputs:
    """Read only persisted same-date ladder inputs and a bounded daily window.

    The positives are the session's close limit-up pool as Fuyao captured it,
    with its ladder; since ``limit_list_ths``/``limit_step`` stopped (decision
    0005) nothing else is the THS pool.  Without a close snapshot there are no
    positives: an intraday pool or another source's close-at-limit rows are a
    different definition and are not substituted; they may only fill fields
    the pool lacks, such as turnover.  This repository does not refresh any
    provider, minute bars, board membership or LHB evidence; the caller passes
    all inputs to the deterministic selector.
    """
    with database.transaction() as connection:
        events = load_close_limit_events(connection, as_of_date)
        limit_rows = enrich_pool_records(events.pool, events.others, trade_date=as_of_date)
        step_rows = events.ladder
        prior_limit_rows = load_prior_close_pool(connection, as_of_date)
        positive_symbols = [str(row["row_data"].get("ts_code") or "").upper() for row in limit_rows]
        control_rows = connection.execute(
            """SELECT b.symbol,b.trading_date,b.open,b.high,b.low,b.close,b.pre_close,b.volume,b.amount,
                      b.is_suspended,b.limit_up,b.limit_down,b.selected_provider,b.available_at,
                      ((b.limit_up-b.close)/NULLIF(b.pre_close,0))*100 AS limit_gap_pct,
                      ((b.limit_up/b.pre_close)-1)*100 AS limit_pct
                 FROM quant.canonical_bars_daily b
                WHERE b.trading_date=%s AND b.pre_close>0 AND b.limit_up>b.pre_close
                  AND b.limit_up<=b.pre_close*1.31 AND b.close<b.limit_up
                  AND b.close/b.limit_up>=0.94 AND b.is_suspended=false
                  AND NOT (b.symbol=ANY(%s))
                ORDER BY ((b.limit_up-b.close)/NULLIF(b.pre_close,0)),b.symbol
                LIMIT 600""",
            (as_of_date, positive_symbols),
        ).fetchall() if positive_symbols else []
        symbols = [*positive_symbols, *(str(row["symbol"] or "").upper() for row in control_rows)]
        symbols = list(dict.fromkeys(symbol for symbol in symbols if symbol))
        daily_rows = connection.execute(
            """WITH ranked AS (
                   SELECT b.*,row_number() OVER(PARTITION BY b.symbol ORDER BY b.trading_date DESC) rn
                     FROM quant.canonical_bars_daily b WHERE b.symbol=ANY(%s)
                      AND b.trading_date<=%s AND b.trading_date>=%s
                 ) SELECT * FROM ranked WHERE rn<=21 ORDER BY symbol,trading_date""",
            (symbols, as_of_date, as_of_date - timedelta(days=60)),
        ).fetchall() if symbols else []
    return StrategyPatternSampleInputs(
        limit_rows=[dict(row) for row in limit_rows],
        step_rows=[dict(row.get("row_data") or row) for row in step_rows],
        prior_limit_rows=[dict(row.get("row_data") or row) for row in prior_limit_rows],
        control_rows=[dict(row) for row in control_rows],
        daily_rows=[dict(row) for row in daily_rows],
    )


def persist_strategy_pattern_run(
    database: Any,
    run_key: str,
    as_of_date: date,
    status: str,
    source_status: dict[str, Any],
    summary: dict[str, Any],
    samples: list[dict[str, Any]],
    *,
    model_version: str,
    json_safe: Any,
) -> Any:
    """Replace one bounded post-close pattern run atomically.

    Inputs are already selected and minute-replayed by the caller.  This
    repository never fetches providers or ranks samples, so persistence cannot
    accidentally widen the replay/data-collection boundary.
    """
    with database.transaction() as connection:
        run = connection.execute(
            """INSERT INTO quant.strategy_pattern_runs(run_key,as_of_date,model_version,status,source_status,summary)
               VALUES(%s,%s,%s,%s,%s,%s)
               ON CONFLICT(run_key) DO UPDATE SET status=EXCLUDED.status,source_status=EXCLUDED.source_status,
                 summary=EXCLUDED.summary,updated_at=now() RETURNING run_id""",
            (run_key, as_of_date, model_version, status,
             Json(json_safe(source_status)), Json(json_safe(summary))),
        ).fetchone()
        connection.execute("DELETE FROM quant.strategy_pattern_samples WHERE run_id=%s", (run["run_id"],))
        for rank, sample in enumerate(samples, start=1):
            connection.execute(
                """INSERT INTO quant.strategy_pattern_samples(run_id,rank,symbol,name,primary_cohort,cohorts,board_context,
                       limit_context,daily_features,intraday_pattern,minute_source,risk_flags)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (run["run_id"], rank, sample["symbol"], sample.get("name"), sample["primary_cohort"], Json(sample["cohorts"]),
                 Json(json_safe(sample["board_context"])), Json(json_safe(sample["limit_context"])),
                 Json(json_safe(sample["daily_features"])), Json(json_safe(sample["intraday_pattern"])),
                 sample.get("minute_source"), Json(sample["risk_flags"])),
            )
    return run["run_id"]


__all__ = [
    "StrategyPatternSampleInputs", "load_strategy_pattern_sample_inputs", "persist_strategy_pattern_run",
]
