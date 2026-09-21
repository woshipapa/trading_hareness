"""Production runtime adapter for the daily strategy summary.

The same evidence-only text is persisted before it becomes a daily Feishu
review receipt. Scheduler timing and terminal semantics stay in
``daily_strategy_summary_scheduler``; delivery never changes strategy state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Awaitable, Callable

from .daily_strategy_summary_scheduler import DailyStrategySummarySchedulerDependencies


@dataclass(frozen=True)
class DailyStrategySummaryRuntimeDependencies:
    database: Any
    run_database: Callable[..., Awaitable[Any]]
    build_summary: Callable[[date], dict[str, Any]]
    summary_text: Callable[[dict[str, Any], str | None], str]
    dashboard_url: Callable[[], str | None]
    json_safe: Callable[[Any], Any]
    json_value: Callable[[Any], Any]
    terminal_for_exchange_date: Callable[[Any, date], bool]
    calendar_open: Callable[[date], Awaitable[bool]]
    now: Callable[[], datetime]
    scheduler: Callable[[DailyStrategySummarySchedulerDependencies], Awaitable[None]]
    post_text: Callable[[str], Awaitable[dict[str, Any]]] | None = None


async def run_daily_strategy_summary(
    exchange_date: date,
    dependencies: DailyStrategySummaryRuntimeDependencies,
) -> dict[str, Any]:
    """Build, persist and optionally deliver one same-date research summary."""
    summary = await dependencies.run_database(dependencies.build_summary, exchange_date)
    text = dependencies.summary_text(summary, dependencies.dashboard_url())

    def persist_pending() -> None:
        with dependencies.database.transaction() as connection:
            connection.execute(
                """INSERT INTO quant.strategy_day_summaries(exchange_date,payload,message_text,delivery_status,
                           attempt_count,next_attempt_at,sent_at,error_message)
                   VALUES(%s,%s,%s,'pending',0,NULL,NULL,NULL)
                   ON CONFLICT(exchange_date) DO UPDATE SET payload=EXCLUDED.payload,
                       message_text=EXCLUDED.message_text,delivery_status='pending',
                       next_attempt_at=NULL,error_message=NULL,updated_at=now()""",
                (exchange_date, dependencies.json_value(dependencies.json_safe(summary)), text),
            )

    if dependencies.post_text is None:
        delivery = {"status": "suppressed", "reason": "Feishu is reserved for watched-stock strategy signals"}
    else:
        # Write a durable pending receipt before crossing the network boundary.
        # A restart can therefore retry a summary that was interrupted before
        # its final delivery status was recorded.
        await dependencies.run_database(persist_pending)
        delivery = await dependencies.post_text(text)
    delivery_status = str(delivery.get("status") or "failed")
    if delivery_status not in {"sent", "failed", "disabled", "suppressed"}:
        delivery_status = "failed"
    delivery_error = delivery.get("error") or delivery.get("reason")

    def persist_delivery() -> None:
        with dependencies.database.transaction() as connection:
            connection.execute(
                """INSERT INTO quant.strategy_day_summaries(exchange_date,payload,message_text,delivery_status,
                           attempt_count,next_attempt_at,sent_at,error_message)
                   VALUES(%s,%s,%s,%s,1,
                           CASE WHEN %s='failed' THEN now()+interval '5 minutes' ELSE NULL END,
                           CASE WHEN %s='sent' THEN now() ELSE NULL END,%s)
                   ON CONFLICT(exchange_date) DO UPDATE SET payload=EXCLUDED.payload,message_text=EXCLUDED.message_text,
                       delivery_status=EXCLUDED.delivery_status,
                       attempt_count=quant.strategy_day_summaries.attempt_count+1,
                       next_attempt_at=EXCLUDED.next_attempt_at, sent_at=EXCLUDED.sent_at,
                       error_message=EXCLUDED.error_message,updated_at=now()""",
                (exchange_date, dependencies.json_value(dependencies.json_safe(summary)), text,
                 delivery_status, delivery_status, delivery_status, delivery_error),
            )
    await dependencies.run_database(persist_delivery)
    result = {"status": delivery_status, "exchange_date": str(exchange_date), "summary": summary,
              "delivery": {"status": delivery_status}}
    if delivery_error:
        result["reason"] = str(delivery_error)
    return result


async def run_daily_strategy_summary_loop(
    dependencies: DailyStrategySummaryRuntimeDependencies,
) -> None:
    """Run the 15:05--22:00 same-date scheduler with durable delivery receipts."""
    async def terminal_for_date(exchange_date: date) -> bool:
        def load() -> bool:
            with dependencies.database.transaction() as connection:
                return dependencies.terminal_for_exchange_date(connection, exchange_date)
        return bool(await dependencies.run_database(load, timeout_seconds=10))

    await dependencies.scheduler(DailyStrategySummarySchedulerDependencies(
        calendar_open=dependencies.calendar_open,
        terminal_for_date=terminal_for_date,
        run_summary=lambda exchange_date: run_daily_strategy_summary(exchange_date, dependencies),
        now=dependencies.now,
    ))


__all__ = [
    "DailyStrategySummaryRuntimeDependencies",
    "run_daily_strategy_summary",
    "run_daily_strategy_summary_loop",
]
