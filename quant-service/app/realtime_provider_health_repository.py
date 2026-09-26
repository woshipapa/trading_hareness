"""Bounded local reads for the independent data-source health view."""

from datetime import datetime
from typing import Any

from .provider_observability import provider_health_snapshot
from .async_provider_status_read_repository import provider_health
from .realtime_provider_health import CN, continuous_session, project_realtime_source_health

SCAN_SQL = """SELECT status,observed_at,requested_symbols,source_status,summary
    FROM quant.intraday_scan_runs
    WHERE status IN ('completed','partial','failed')
    ORDER BY observed_at DESC LIMIT 1"""
CALENDAR_SQL = "SELECT is_open FROM quant.market_trade_calendar WHERE exchange='SSE' AND calendar_date=%s"


def project(snapshot, scan, calendar, configs, now):
    payload = project_realtime_source_health(snapshot, scan, provider_configs=configs,
        session_active=continuous_session(now, bool(calendar['is_open']) if calendar else None))
    # Historical ledger rows remain separately identified; they cannot turn a
    # missing realtime observation green.
    payload['provider_history'] = snapshot['items']
    return payload


def realtime_provider_health_snapshot(database: Any, configs: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    snapshot = provider_health_snapshot(database, configs, now)
    with database.transaction() as connection:
        scan = connection.execute(SCAN_SQL).fetchone()
        calendar = connection.execute(CALENDAR_SQL, (now.astimezone(CN).date(),)).fetchone()
    return project(snapshot, scan, calendar, configs, now)


async def realtime_provider_health(async_database: Any, configs: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    snapshot = await provider_health(async_database, configs, now)
    async with async_database.transaction() as connection:
        scan = await (await connection.execute(SCAN_SQL)).fetchone()
        calendar = await (await connection.execute(CALENDAR_SQL, (now.astimezone(CN).date(),))).fetchone()
    return project(snapshot, scan, calendar, configs, now)
