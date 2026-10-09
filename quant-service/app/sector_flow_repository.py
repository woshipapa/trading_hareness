"""Materialize daily 同花顺 concept-flow migration with exact-member LHB context.

Two flow taxonomies are materialized, live first:

* ``eastmoney_concept`` - the closing-window snapshot of the one-minute board
  capture.  Despite its key it holds 同花顺 boards (akshare reads
  data.10jqka.com.cn), keyed by board *name*, net inflow in 亿元.  Its LHB and
  limit-up context is counted over the Fuyao-refreshed ``fuyao_ths_concept``
  membership, reached by name through :mod:`ths_concept_name_bridge`; a board
  whose name does not match is materialized without that context and named in
  the result's coverage.  It has no board close level, so its outcomes chain
  the boards' daily change, and only across consecutive capture sessions.
* ``ths_concept_flow`` - the Tushare-era rows, frozen since 2026-10-08,
  rebuilt only for the dates they cover, with their own membership and closes.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from statistics import median
from typing import Any

from psycopg.types.json import Json

from .sector_flow_features import sector_flow_feature, sector_flow_outcome
from .ths_concept_name_bridge import CATALOG_SQL, CATALOGS, build_bridge
from .sector_membership_repository import point_in_time_membership_predicate


def _sign(value: Any) -> int:
    number = float(value or 0)
    return 1 if number > 0 else -1 if number < 0 else 0


#: Flow taxonomy -> the membership its LHB and limit-up context is counted over.
FLOW_MEMBERSHIP = {"eastmoney_concept": "fuyao_ths_concept", "ths_concept_flow": "ths_concept_flow"}


def rebuild_sector_flow_daily_features(database: Any, start_date: date, end_date: date) -> dict[str, Any]:
    if end_date < start_date or (end_date - start_date).days > 45:
        raise ValueError("sector-flow feature rebuild requires an ordered range capped at 45 days")
    passes = {taxonomy: _rebuild_taxonomy(database, taxonomy, membership, start_date, end_date)
              for taxonomy, membership in FLOW_MEMBERSHIP.items()}
    transitions: dict[str, int] = {}
    for result in passes.values():
        for transition, count in result["transition_counts"].items():
            transitions[transition] = transitions.get(transition, 0) + count
    outcomes = materialize_sector_flow_daily_outcomes(database, end_date)
    return {
        "status": "completed", "start_date": str(start_date), "end_date": str(end_date),
        "stored": sum(result["stored"] for result in passes.values()), "transition_counts": transitions,
        "taxonomies": passes, "source": "stored_evidence_only",
        "provider_calls": 0, "outcomes": outcomes, "decision_eligible": False,
    }


def _rebuild_taxonomy(database: Any, taxonomy_key: str, membership_taxonomy: str,
                      start_date: date, end_date: date) -> dict[str, Any]:
    context_start = start_date - timedelta(days=10)
    coverage: dict[str, Any] | None = None
    membership_predicate_lhb = point_in_time_membership_predicate("m", "l.day")
    membership_predicate_event = point_in_time_membership_predicate("m", "event.day")
    with database.transaction() as connection:
        flow_rows = connection.execute(
            """SELECT DISTINCT ON(trading_date,sector_key)
                      trading_date,sector_key,provider_key,available_at,change_pct,net_amount,
                      net_buy_amount,net_sell_amount
                 FROM quant.sector_market_observations
                WHERE taxonomy_key=%s AND trading_date BETWEEN %s AND %s
                ORDER BY trading_date,sector_key,
                         CASE provider_key WHEN 'tushare_super_sdk' THEN 0
                                           WHEN 'tushare_super_get' THEN 1 ELSE 2 END,
                         available_at DESC""",
            (taxonomy_key, context_start, end_date),
        ).fetchall()
        name_by_code: dict[str, str] | None = None
        if membership_taxonomy != taxonomy_key:
            # Name-keyed 同花顺 flow: reach the coded membership through the board name.
            bridge = build_bridge("concept", connection.execute(CATALOG_SQL, (list(CATALOGS["concept"]),)).fetchall())
            matched, coverage = bridge.match(str(dict(row)["sector_key"]) for row in flow_rows)
            name_by_code = {}
            for name, code in matched.items():
                name_by_code.setdefault(code, name)
        # One list row per stock and day.  The Fuyao list, dated by its own
        # trade_date because a late list is captured on a later evening, is
        # preferred to AKShare's lhb_event rows, dated by capture.  A Fuyao row
        # is already unique per stock and day, so it needs no amount rank; a
        # net that is not a JSON number stays unknown instead of failing.
        lhb_rows = connection.execute(
            f"""WITH event_json AS (
                   SELECT occurred_at,symbol,available_at,
                          CASE WHEN body IS JSON THEN body::jsonb END AS payload
                     FROM quant.market_events WHERE event_type='lhb_event'
               ), fuyao_json AS (
                   SELECT symbol,available_at,CASE WHEN body IS JSON THEN body::jsonb END AS payload
                     FROM quant.market_events WHERE event_type='lhb_ths' AND source='fuyao_ths'
               ), lhb_candidates AS (
                   SELECT (occurred_at AT TIME ZONE 'Asia/Shanghai')::date AS day,symbol,
                          COALESCE(NULLIF(payload->>'龙虎榜净买额','')::numeric,0) AS net_amount,
                          COALESCE(NULLIF(payload->>'龙虎榜成交额','')::numeric,0) AS lhb_amount,
                          1 AS source_priority,available_at
                     FROM event_json WHERE payload IS NOT NULL
                   UNION ALL
                   SELECT CASE WHEN payload->>'trade_date' ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$'
                               THEN (payload->>'trade_date')::date END AS day,symbol,
                          CASE WHEN jsonb_typeof(payload->'net_value')='number'
                               THEN (payload->>'net_value')::numeric END AS net_amount,
                          NULL::numeric AS lhb_amount,
                          0 AS source_priority,available_at
                     FROM fuyao_json WHERE payload IS NOT NULL
               ), one_per_symbol AS (
                   SELECT DISTINCT ON(day,symbol) day,symbol,net_amount
                     FROM lhb_candidates WHERE day BETWEEN %s AND %s
                    ORDER BY day,symbol,source_priority,lhb_amount DESC,available_at DESC
               ), aggregate AS (
                   SELECT l.day,m.sector_key,count(*) AS stock_count,
                          sum(l.net_amount) AS net_amount,
                          count(*) FILTER (WHERE l.net_amount<0) AS negative_count
                     FROM one_per_symbol l
                     JOIN quant.sector_membership_history m
                       ON m.taxonomy_key=%s AND m.symbol=l.symbol
                      AND {membership_predicate_lhb}
                    GROUP BY l.day,m.sector_key
               ), limits AS (
                   SELECT day,m.sector_key,count(DISTINCT event.symbol) AS limit_up_count
                     FROM (
                         SELECT symbol,(occurred_at AT TIME ZONE 'Asia/Shanghai')::date AS day
                           FROM quant.market_events WHERE event_type='limit_up_pool'
                     ) event
                     JOIN quant.sector_membership_history m
                       ON m.taxonomy_key=%s AND m.symbol=event.symbol
                      AND {membership_predicate_event}
                    WHERE day BETWEEN %s AND %s GROUP BY day,m.sector_key
               )
               SELECT COALESCE(a.day,l.day) AS trading_date,
                      COALESCE(a.sector_key,l.sector_key) AS sector_key,
                      COALESCE(a.stock_count,0) AS stock_count,
                      COALESCE(a.net_amount,0) AS net_amount,
                      COALESCE(a.negative_count,0) AS negative_count,
                      COALESCE(l.limit_up_count,0) AS limit_up_count
                 FROM aggregate a FULL JOIN limits l USING(day,sector_key)""",
            (start_date, end_date, membership_taxonomy, membership_taxonomy, start_date, end_date),
        ).fetchall()

    flows_by_sector: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_day: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for raw in flow_rows:
        row = dict(raw)
        flows_by_sector[str(row["sector_key"])].append(row)
        by_day[row["trading_date"]].append(row)
    lhb_map: dict[tuple[date, str], dict[str, Any]] = {}
    for raw in lhb_rows:
        row = dict(raw)
        key = str(row["sector_key"]) if name_by_code is None else name_by_code.get(str(row["sector_key"]))
        if key is not None:
            lhb_map[(row["trading_date"], key)] = row
    rank_map: dict[tuple[date, str], float | None] = {}
    for day, rows in by_day.items():
        ranked = sorted((row for row in rows if row["net_amount"] is not None), key=lambda row: float(row["net_amount"]))
        denominator = max(1, len(ranked) - 1)
        for index, row in enumerate(ranked):
            rank_map[(day, str(row["sector_key"]))] = index / denominator

    mapped_names = set((name_by_code or {}).values())
    stored = 0
    transitions: dict[str, int] = {}
    with database.transaction() as connection:
        for sector_key, rows in flows_by_sector.items():
            rows.sort(key=lambda row: row["trading_date"])
            streak = 0
            last_sign = 0
            for index, row in enumerate(rows):
                current_sign = _sign(row["net_amount"])
                streak = streak + current_sign if current_sign and current_sign == last_sign else current_sign
                last_sign = current_sign
                if row["trading_date"] < start_date:
                    continue
                feature = sector_flow_feature(
                    row,
                    previous=rows[index - 1] if index >= 1 else None,
                    prior=rows[index - 2] if index >= 2 else None,
                    rank_percentile=rank_map.get((row["trading_date"], sector_key)),
                    sign_streak=streak,
                    lhb=lhb_map.get((row["trading_date"], sector_key)),
                )
                quality_flags = []
                if index == 0:
                    quality_flags.append("previous_flow_missing")
                if name_by_code is not None and sector_key not in mapped_names:
                    quality_flags.append("membership_name_unmatched")
                connection.execute(
                    """INSERT INTO quant.sector_flow_daily_features(
                           taxonomy_key,sector_key,trading_date,provider_key,available_at,status,transition,
                           net_amount,previous_net_amount,net_change_amount,net_acceleration,rank_percentile,
                           flow_sign_streak,change_pct,price_flow_divergence,lhb_stock_count,lhb_net_amount,
                           lhb_negative_count,lhb_sell_pressure_ratio,limit_up_count,features,quality_flags)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(taxonomy_key,sector_key,trading_date) DO UPDATE SET
                         provider_key=EXCLUDED.provider_key,available_at=EXCLUDED.available_at,status=EXCLUDED.status,
                         transition=EXCLUDED.transition,net_amount=EXCLUDED.net_amount,
                         previous_net_amount=EXCLUDED.previous_net_amount,net_change_amount=EXCLUDED.net_change_amount,
                         net_acceleration=EXCLUDED.net_acceleration,rank_percentile=EXCLUDED.rank_percentile,
                         flow_sign_streak=EXCLUDED.flow_sign_streak,change_pct=EXCLUDED.change_pct,
                         price_flow_divergence=EXCLUDED.price_flow_divergence,lhb_stock_count=EXCLUDED.lhb_stock_count,
                         lhb_net_amount=EXCLUDED.lhb_net_amount,lhb_negative_count=EXCLUDED.lhb_negative_count,
                         lhb_sell_pressure_ratio=EXCLUDED.lhb_sell_pressure_ratio,limit_up_count=EXCLUDED.limit_up_count,
                         features=EXCLUDED.features,quality_flags=EXCLUDED.quality_flags,updated_at=now()""",
                    (
                        taxonomy_key,sector_key,row["trading_date"],row["provider_key"],row["available_at"],
                        "partial" if quality_flags else "ready",feature["transition"],feature["net_amount"],
                        feature["previous_net_amount"],feature["net_change_amount"],feature["net_acceleration"],
                        feature["rank_percentile"],feature["flow_sign_streak"],row["change_pct"],
                        feature["price_flow_divergence"],feature["lhb_stock_count"],feature["lhb_net_amount"],
                        feature["lhb_negative_count"],feature["lhb_sell_pressure_ratio"],feature["limit_up_count"],
                        Json(feature),Json(quality_flags),
                    ),
                )
                stored += 1
                transitions[feature["transition"]] = transitions.get(feature["transition"], 0) + 1
    result: dict[str, Any] = {"stored": stored, "transition_counts": transitions,
                              "membership_taxonomy_key": membership_taxonomy}
    if coverage is not None:
        result["membership_join"] = {**coverage, "unmatched_count": len(coverage["unmatched"]),
                                     "unmatched": coverage["unmatched"][:50]}
    return result


#: Outcome rows sent per pipelined batch. The writer used one round trip a row:
#: every evening it rewrites the whole history (tens of thousands of rows), which
#: over the owner's tunnel (52 ms to the database) took most of an hour. On
#: 2026-10-09 it overran the stage budget, and its thread kept the transaction open
#: for 47 minutes, holding the rows the next run's stage was waiting to write.
OUTCOME_BATCH = 2_000
#: A row whose values did not change is left alone: no new row version, no index
#: churn. Readers aggregate the outcomes and never look at updated_at.
OUTCOME_UPSERT = """INSERT INTO quant.sector_flow_daily_outcomes(
       taxonomy_key,sector_key,signal_date,horizon_days,transition,status,
       entry_date,exit_date,entry_close,exit_close,raw_return,cross_section_excess_return,
       directional_return,outcome_available_at,quality_flags)
   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
   ON CONFLICT(taxonomy_key,sector_key,signal_date,horizon_days) DO UPDATE SET
     transition=EXCLUDED.transition,status=EXCLUDED.status,entry_date=EXCLUDED.entry_date,
     exit_date=EXCLUDED.exit_date,entry_close=EXCLUDED.entry_close,exit_close=EXCLUDED.exit_close,
     raw_return=EXCLUDED.raw_return,cross_section_excess_return=EXCLUDED.cross_section_excess_return,
     directional_return=EXCLUDED.directional_return,
     outcome_available_at=EXCLUDED.outcome_available_at,quality_flags=EXCLUDED.quality_flags,
     updated_at=now()
   WHERE (quant.sector_flow_daily_outcomes.transition,quant.sector_flow_daily_outcomes.status,
          quant.sector_flow_daily_outcomes.entry_date,quant.sector_flow_daily_outcomes.exit_date,
          quant.sector_flow_daily_outcomes.entry_close,quant.sector_flow_daily_outcomes.exit_close,
          quant.sector_flow_daily_outcomes.raw_return,quant.sector_flow_daily_outcomes.cross_section_excess_return,
          quant.sector_flow_daily_outcomes.directional_return,quant.sector_flow_daily_outcomes.outcome_available_at,
          quant.sector_flow_daily_outcomes.quality_flags)
     IS DISTINCT FROM
         (EXCLUDED.transition,EXCLUDED.status,EXCLUDED.entry_date,EXCLUDED.exit_date,EXCLUDED.entry_close,
          EXCLUDED.exit_close,EXCLUDED.raw_return,EXCLUDED.cross_section_excess_return,EXCLUDED.directional_return,
          EXCLUDED.outcome_available_at,EXCLUDED.quality_flags)"""


def materialize_sector_flow_daily_outcomes(database: Any, as_of_date: date) -> dict[str, Any]:
    """Settle 1/3/5-observation close responses using only stored daily rows."""
    with database.transaction() as connection:
        feature_rows = connection.execute(
            """SELECT taxonomy_key,sector_key,trading_date,transition
                 FROM quant.sector_flow_daily_features
                WHERE taxonomy_key=ANY(%s) AND trading_date<=%s
                ORDER BY taxonomy_key,sector_key,trading_date""",
            (list(FLOW_MEMBERSHIP), as_of_date),
        ).fetchall()
        price_rows = connection.execute(
            """SELECT DISTINCT ON(trading_date,sector_key) trading_date,sector_key,close,available_at
                 FROM quant.sector_market_observations
                WHERE taxonomy_key='ths_concept_flow' AND trading_date<=%s AND close>0
                ORDER BY trading_date,sector_key,
                         CASE provider_key WHEN 'tushare_super_sdk' THEN 0
                                           WHEN 'tushare_super_get' THEN 1 ELSE 2 END,
                         available_at DESC""",
            (as_of_date,),
        ).fetchall()
        change_rows = connection.execute(
            """SELECT DISTINCT ON(trading_date,sector_key) trading_date,sector_key,change_pct,available_at
                 FROM quant.sector_market_observations
                WHERE taxonomy_key='eastmoney_concept' AND trading_date<=%s
                ORDER BY trading_date,sector_key,available_at DESC""",
            (as_of_date,),
        ).fetchall()
    prices: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for raw in price_rows:
        prices[("ths_concept_flow", str(raw["sector_key"]))].append(dict(raw))
    for rows in prices.values():
        rows.sort(key=lambda row: row["trading_date"])
    prices.update({("eastmoney_concept", key): rows for key, rows in chained_levels(change_rows).items()})
    provisional: list[dict[str, Any]] = []
    horizons = (1, 3, 5)
    for raw in feature_rows:
        feature = dict(raw)
        series = prices.get((str(feature["taxonomy_key"]), str(feature["sector_key"])), [])
        position = next((index for index, row in enumerate(series) if row["trading_date"] == feature["trading_date"]), None)
        synthetic = feature["taxonomy_key"] == "eastmoney_concept"
        for horizon in horizons:
            item = {
                **feature, "horizon_days": horizon, "entry_date": feature["trading_date"],
                "entry_close": series[position]["close"] if position is not None else None,
                "exit_date": None, "exit_close": None, "outcome_available_at": None,
                "quality_flags": ["synthetic_level_from_change_pct"] if synthetic else [],
            }
            if (position is not None and position + horizon < len(series)
                    and series[position + horizon].get("segment") != series[position].get("segment")):
                # A session without this board's change sits between entry and exit.
                item.update(status="unavailable", raw_return=None, excess_return=None, directional_return=None,
                            expected_direction=0)
                item["quality_flags"] = [*item["quality_flags"], "change_chain_broken"]
            elif position is not None and position + horizon < len(series):
                exit_row = series[position + horizon]
                item.update(exit_date=exit_row["trading_date"], exit_close=exit_row["close"],
                            outcome_available_at=exit_row["available_at"])
                item.update(sector_flow_outcome(feature["transition"], item["entry_close"], item["exit_close"]))
            elif position is None:
                item.update(status="unavailable", raw_return=None, excess_return=None, directional_return=None,
                            expected_direction=0)
            else:
                item.update(status="pending", raw_return=None, excess_return=None, directional_return=None,
                            expected_direction=0)
            provisional.append(item)
    medians: dict[tuple[str, date, int], float] = {}
    groups: dict[tuple[str, date, int], list[float]] = defaultdict(list)
    for item in provisional:
        if item["status"] == "matured" and item["raw_return"] is not None:
            groups[(item["taxonomy_key"], item["trading_date"], item["horizon_days"])].append(float(item["raw_return"]))
    for key, values in groups.items():
        medians[key] = median(values)
    status_counts: dict[str, int] = {}
    values: list[tuple[Any, ...]] = []
    for item in provisional:
        if item["status"] == "matured":
            benchmark = medians.get((item["taxonomy_key"], item["trading_date"], item["horizon_days"]))
            evaluated = sector_flow_outcome(
                item["transition"], item["entry_close"], item["exit_close"],
                cross_section_median_return=benchmark,
            )
            item.update(evaluated)
        values.append((
            item["taxonomy_key"],item["sector_key"],item["trading_date"],item["horizon_days"],
            item["transition"],item["status"],item["entry_date"],item["exit_date"],item["entry_close"],
            item["exit_close"],item.get("raw_return"),item.get("excess_return"),
            item.get("directional_return"),item["outcome_available_at"],Json(item["quality_flags"]),
        ))
        status_counts[item["status"]] = status_counts.get(item["status"], 0) + 1
    with database.transaction() as connection:
        with connection.cursor() as cursor:
            for first in range(0, len(values), OUTCOME_BATCH):
                cursor.executemany(OUTCOME_UPSERT, values[first:first + OUTCOME_BATCH])
    return {"status": "completed", "as_of_date": str(as_of_date), "rows": len(provisional),
            "status_counts": status_counts, "horizons": list(horizons), "response_type": "close_to_later_close",
            "decision_eligible": False}


def chained_levels(change_rows: list[Any]) -> dict[str, list[dict[str, Any]]]:
    """A level series per board from its daily change, over the capture's own sessions.

    The name-keyed 同花顺 flow carries no board close, only the day's change.
    Chaining ``1 + change/100`` gives a level whose ratios are the board's
    returns - but only across consecutive sessions: a session the capture has
    and the board lacks (or holds without a change) breaks the chain, and a
    new segment starts at the next session the board has.
    """
    rows = [dict(row) for row in change_rows]
    sessions = sorted({row["trading_date"] for row in rows})
    by_board: dict[str, dict[date, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        by_board[str(row["sector_key"])][row["trading_date"]] = row
    levels: dict[str, list[dict[str, Any]]] = {}
    for board, days in by_board.items():
        series: list[dict[str, Any]] = []
        segment, level = 0, None
        for session in sessions[sessions.index(min(days)):]:
            row = days.get(session)
            change = row.get("change_pct") if row else None
            if row is None or change is None:
                segment, level = segment + 1, None
                series.append({"trading_date": session, "close": None, "available_at": None, "segment": segment})
                continue
            level = 1.0 if level is None else level * (1 + float(change) / 100)
            series.append({"trading_date": session, "close": level, "available_at": row["available_at"],
                           "segment": segment})
        levels[board] = series
    return levels


__all__ = ["FLOW_MEMBERSHIP", "chained_levels", "materialize_sector_flow_daily_outcomes", "rebuild_sector_flow_daily_features"]
