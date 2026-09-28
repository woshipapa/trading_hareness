"""Read-only daily strategy summary projection, independent of HTTP wiring."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable


LEARNING_WINDOW_LIMIT = 10_000


def apply_exchange_date_data_gate(
    readiness: Any,
    *,
    expected_daily_bar_date: date | None,
    latest_daily_bar_date: date | None,
    market_snapshot_decision_eligible: bool | None,
) -> dict[str, Any]:
    """Overlay same-date evidence on the historical feature-readiness result.

    Feature coverage can be complete while today's close snapshot or the
    previous session's daily bars are missing.  Keeping this gate explicit
    prevents a summary from presenting historical readiness as today's
    decision eligibility.
    """
    result = dict(readiness or {})
    blockers = list(result.get("blockers") or [])
    if (expected_daily_bar_date is not None
            and (latest_daily_bar_date is None or latest_daily_bar_date < expected_daily_bar_date)):
        blockers.append("daily_bars_stale_for_exchange_date")
    if market_snapshot_decision_eligible is False:
        blockers.append("market_snapshot_not_decision_eligible")
    blockers = list(dict.fromkeys(str(item) for item in blockers))
    result["decision_ready"] = not blockers
    result["blockers"] = blockers
    result["exchange_date_gate"] = {
        "expected_daily_bar_date": str(expected_daily_bar_date) if expected_daily_bar_date else None,
        "latest_daily_bar_date": str(latest_daily_bar_date) if latest_daily_bar_date else None,
        "market_snapshot_decision_eligible": market_snapshot_decision_eligible,
    }
    return result


def build_daily_strategy_summary(database: Any, exchange_date: date, *, readiness: Callable[[Any], Any],
                                 json_safe: Callable[[Any], Any], policy_review: Callable[..., Any]) -> dict[str, Any]:
    with database.transaction() as connection:
        signal_rows = connection.execute(
            """SELECT state,count(*)::int AS count FROM quant.intraday_signal_events
                 WHERE observed_at AT TIME ZONE 'Asia/Shanghai' >= %s
                   AND observed_at AT TIME ZONE 'Asia/Shanghai' < %s GROUP BY state""",
            (exchange_date, exchange_date + timedelta(days=1)),
        ).fetchall()
        outcome_rows = connection.execute(
            """SELECT horizon_key,status,count(*)::int AS count FROM quant.intraday_signal_outcomes
                 WHERE entry_observed_at AT TIME ZONE 'Asia/Shanghai' >= %s
                   AND entry_observed_at AT TIME ZONE 'Asia/Shanghai' < %s GROUP BY horizon_key,status""",
            (exchange_date, exchange_date + timedelta(days=1)),
        ).fetchall()
        learning_rows = connection.execute(
            """SELECT s.signal_event_id,s.signal_type,s.observed_at,s.evidence,
                      (s.observed_at AT TIME ZONE 'Asia/Shanghai')::date AS exchange_date,
                      o.status,o.raw_return,o.maximum_favorable_excursion,o.maximum_adverse_excursion
                 FROM quant.intraday_signal_events s
                 LEFT JOIN quant.intraday_signal_outcomes o
                   ON o.signal_event_id=s.signal_event_id AND o.horizon_key='30m'
                WHERE s.state='alerted' AND s.signal_type IN ('entry','watch','reduce','exit')
                ORDER BY s.observed_at DESC LIMIT %s""", (LEARNING_WINDOW_LIMIT + 1,),
        ).fetchall()
        post_close_run = connection.execute(
            """SELECT run_id,status,summary FROM quant.post_close_strategy_runs
                 WHERE as_of_date=%s ORDER BY updated_at DESC LIMIT 1""", (exchange_date,),
        ).fetchone()
        candidates = []
        if post_close_run:
            candidates = connection.execute(
                """SELECT c.symbol,i.name,c.candidate_type,c.score
                     FROM quant.post_close_strategy_candidates c LEFT JOIN quant.instruments i ON i.symbol=c.symbol
                    WHERE c.run_id=%s ORDER BY c.rank LIMIT 5""", (post_close_run["run_id"],),
            ).fetchall()
        close_review = connection.execute(
            """SELECT market_state,data_boundary FROM quant.strategy_review_runs
                 WHERE exchange_date=%s AND session='close' ORDER BY observed_at DESC LIMIT 1""", (exchange_date,),
        ).fetchone()
        readiness_result = readiness(connection)
        expected_daily_bar = connection.execute(
            """SELECT max(calendar_date) expected_date FROM quant.market_trade_calendar
                WHERE is_open AND calendar_date < %s""", (exchange_date,),
        ).fetchone()
        latest_daily_bar = connection.execute(
            """SELECT max(trading_date) latest_date FROM quant.canonical_bars_daily
                WHERE symbol<>'000300.SH' AND adj_factor>0"""
        ).fetchone()
        market_snapshot = connection.execute(
            """SELECT decision_eligible FROM quant.market_snapshot_runs
                WHERE exchange_date=%s ORDER BY observed_at DESC LIMIT 1""", (exchange_date,),
        ).fetchone()
        readiness_result = apply_exchange_date_data_gate(
            readiness_result,
            expected_daily_bar_date=(expected_daily_bar or {}).get("expected_date"),
            latest_daily_bar_date=(latest_daily_bar or {}).get("latest_date"),
            market_snapshot_decision_eligible=(market_snapshot or {}).get("decision_eligible"),
        )
    signal_counts = {str(row["state"]): int(row["count"] or 0) for row in signal_rows}
    outcome_counts: dict[str, dict[str, int]] = {}
    for row in outcome_rows:
        outcome_counts.setdefault(str(row["horizon_key"]), {})[str(row["status"])] = int(row["count"] or 0)
    learning_truncated = len(learning_rows) > LEARNING_WINDOW_LIMIT
    learning_rows = list(reversed(learning_rows[:LEARNING_WINDOW_LIMIT]))
    learning_input = [{**json_safe(dict(row)), "exchange_date": str(exchange_date)} for row in learning_rows]
    policy_learning = policy_review(learning_input, focus_exchange_date=str(exchange_date))
    policy_learning["source_window"] = {
        "limit": LEARNING_WINDOW_LIMIT,
        "rows": len(learning_input),
        "truncated": learning_truncated,
        "ordering": "latest_first_query_replayed_oldest_first",
        "notice": "离线策略复盘使用有界信号窗口；完整信号与结果仍保留在本地账本，可按窗口重算。",
    }
    return {
        "exchange_date": str(exchange_date), "signal_counts": signal_counts,
        "outcome_counts": outcome_counts,
        "post_close": {
            "status": post_close_run["status"] if post_close_run else "missing",
            "reason": ((post_close_run["summary"] or {}).get("reason") if post_close_run else "post-close strategy has not produced a run"),
            "candidates": [dict(row) for row in candidates],
        },
        "close_review": json_safe(dict(close_review)) if close_review else None,
        "readiness": readiness_result,
        "offline_policy_learning": policy_learning,
    }


def terminal_for_exchange_date(connection: Any, exchange_date: date) -> bool:
    """Whether the persisted day summary is a restart-safe terminal receipt.

    ``suppressed`` is retained for deployments that explicitly disable Feishu.
    A summary whose post-close candidate stage was blocked is therefore still
    retryable: late daily bars may make that stage complete later in the same
    evening.
    """
    row = connection.execute(
        """SELECT 1 FROM quant.strategy_day_summaries
             WHERE exchange_date=%s AND delivery_status=ANY(%s)
               AND NOT (
                   delivery_status='suppressed'
                   AND COALESCE(payload->'post_close'->>'status','')='blocked'
               )
             LIMIT 1""",
        (exchange_date, ["sent", "disabled", "suppressed"]),
    ).fetchone()
    return row is not None


__all__ = [
    "LEARNING_WINDOW_LIMIT", "apply_exchange_date_data_gate", "build_daily_strategy_summary",
    "terminal_for_exchange_date",
]
