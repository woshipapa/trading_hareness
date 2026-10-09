"""Read-only projection for post-close limit-pool pattern mining evidence."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable

from .limit_continuation_research import continuation_watch, rank_continuation_candidates
from .dragon_leader_research import enrich_dragon_leader_watches, rank_dragon_leader_candidates
from .limit_event_fallback import CHAIN_LADDER_SOURCE, POOL_TAG_LADDER_SOURCE
from .limit_event_repository import load_close_limit_events


def latest_strategy_pattern_mining(
    database: Any,
    merge_limit_pool_sources_fn: Callable[..., dict[str, Any]],
    limit_board_count_fn: Callable[[Any], int],
    strategy_json_safe_fn: Callable[[Any], Any],
    post_close_limit_daily_features_fn: Callable[[list[dict[str, Any]]], dict[str, Any]],
    post_close_exact_board_context_fn: Callable[[Any], dict[str, Any]],
    post_close_tushare_lhb_context_fn: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    """Project only already-persisted pattern evidence; never refresh sources.

    The pool is the Fuyao close snapshot (the THS pool the retired
    ``limit_list_ths`` used to deliver) merged with the other sources'
    ``limit_up_pool`` events of the day; the ladder is the Fuyao ladder for
    names still in the close pool.  ``pool_coverage.close_snapshot`` says
    which snapshot was used, or why none was.
    """
    with database.transaction() as connection:
        run = connection.execute(
            """SELECT run_id,run_key,as_of_date,model_version,status,source_status,summary,created_at,updated_at
                 FROM quant.strategy_pattern_runs ORDER BY as_of_date DESC,updated_at DESC LIMIT 1"""
        ).fetchone()
        if not run:
            return {"run": None, "limit_pool": [], "limit_ladder": [], "continuation_candidates": [], "dragon_leader_candidates": [], "dragon_leader_market_context": {}, "pool_coverage": {}, "picks": [], "samples": [],
                    "notice": "尚未运行涨停梯队与分钟拉升形态挖掘。"}
        rows = connection.execute(
            """SELECT rank,symbol,name,primary_cohort,cohorts,board_context,limit_context,daily_features,
                      intraday_pattern,minute_source,risk_flags
                 FROM quant.strategy_pattern_samples WHERE run_id=%s ORDER BY rank""", (run["run_id"],)
        ).fetchall()
        events = load_close_limit_events(connection, run["as_of_date"])
    union = merge_limit_pool_sources_fn(events.pool, events.others)
    pool = [{**item, "board_count": limit_board_count_fn(item.get("tag"))} for item in union["items"]]
    pool.sort(key=lambda item: (-int(item.get("board_count") or 0), -float(item.get("limit_amount") or 0), str(item.get("ts_code") or "")))
    symbols = [str(item.get("ts_code") or "") for item in pool]
    with database.transaction() as connection:
        daily_records = connection.execute(
            """WITH ranked AS (
                   SELECT b.*,row_number() OVER(PARTITION BY b.symbol ORDER BY b.trading_date DESC) rn
                     FROM quant.canonical_bars_daily b
                    WHERE b.symbol=ANY(%s) AND b.trading_date<=%s AND b.trading_date>=%s
                 ) SELECT * FROM ranked WHERE rn<=21 ORDER BY symbol,trading_date""",
            (symbols, run["as_of_date"], run["as_of_date"] - timedelta(days=60)),
        ).fetchall() if symbols else []
    daily_grouped: dict[str, list[dict[str, Any]]] = {}
    for record in daily_records:
        daily_grouped.setdefault(str(record["symbol"]), []).append(dict(record))
    board_contexts = post_close_exact_board_context_fn(run["as_of_date"])
    lhb_contexts = post_close_tushare_lhb_context_fn(run["as_of_date"])
    for item in pool:
        symbol = str(item.get("ts_code") or "")
        daily = post_close_limit_daily_features_fn(daily_grouped.get(symbol, []))
        item.update({"daily_features": daily, "volume_multiple_5d": daily.get("volume_multiple_5d"),
                     "volume_multiple_20d": daily.get("volume_multiple_20d"), "low_pct": daily.get("low_pct"),
                     "board_context": board_contexts.get(symbol), "lhb_context": lhb_contexts.get(symbol)})
        item["continuation_watch"] = continuation_watch(
            item, number=lambda value: float(value) if value is not None else None,
            board_count=limit_board_count_fn,
        )
    dragon_leader_market_context = enrich_dragon_leader_watches(pool)
    pool_by_symbol = {str(item.get("ts_code") or ""): item for item in pool}
    limit_pool = [{**item, "rank": rank} for rank, item in enumerate(pool, start=1)]
    ladder_by_symbol: dict[str, dict[str, Any]] = {}
    for symbol, context in pool_by_symbol.items():
        if int(context.get("board_count") or 0) >= 2:
            ladder_by_symbol[symbol] = {**context, "nums": int(context.get("board_count") or 0),
                                         "ladder_sources": [POOL_TAG_LADDER_SOURCE]}
    for record in events.ladder:
        item = strategy_json_safe_fn(dict(record["row_data"] or {}))
        symbol = str(item.get("ts_code") or "")
        context = pool_by_symbol.get(symbol, {})
        ladder_by_symbol[symbol] = {
            **context, **item, "provider_key": record["provider_key"], "available_at": record["available_at"],
            "tag": context.get("tag") or item.get("tag"), "status": context.get("status"),
            "price": context.get("price"), "pct_chg": context.get("pct_chg"),
            "turnover_rate": context.get("turnover_rate"), "open_num": context.get("open_num"),
            "limit_amount": context.get("limit_amount"), "lu_desc": context.get("lu_desc"),
            "volume_multiple_5d": context.get("volume_multiple_5d"), "volume_multiple_20d": context.get("volume_multiple_20d"),
            "board_context": context.get("board_context"), "lhb_context": context.get("lhb_context"),
            "ladder_sources": list(dict.fromkeys([*(ladder_by_symbol.get(symbol, {}).get("ladder_sources") or []),
                                                     CHAIN_LADDER_SOURCE])),
        }
    ladder = list(ladder_by_symbol.values())
    ladder.sort(key=lambda item: (-int(item.get("nums") or 0), -float(item.get("limit_amount") or 0), str(item.get("ts_code") or "")))
    limit_ladder = [{**item, "rank": rank} for rank, item in enumerate(ladder, start=1)]
    continuation_candidates = rank_continuation_candidates(pool)
    dragon_leader_candidates = rank_dragon_leader_candidates(pool)
    union["coverage"].update({"limit_step_count": len(events.ladder), "multi_board_union_count": len(limit_ladder),
                              "close_snapshot": events.snapshot})
    sample_items = [dict(row) for row in rows]
    picks = [item for item in sample_items
             if (item.get("limit_context") or {}).get("sample_role") != "matched_near_limit_control"
             and (item.get("limit_context") or {}).get("review_tier") != "research_sample"][:10]
    return {"run": run, "limit_pool": limit_pool, "limit_ladder": limit_ladder,
            "continuation_candidates": continuation_candidates, "dragon_leader_candidates": dragon_leader_candidates,
            "dragon_leader_market_context": dragon_leader_market_context, "pool_coverage": union["coverage"],
            "picks": picks, "samples": sample_items,
            "notice": "地天板、龙头、连板和首板均为盘后研究样本；实时阶段仍需点时量价、板块联动与承接确认。"}


__all__ = ["latest_strategy_pattern_mining"]
