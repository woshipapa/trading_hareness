"""Standalone entry point for the data-source layer.

    python -m app.datasources catalog [--capability limits.limit_up_pool] [--source fuyao_ths]
    python -m app.datasources validate
    python -m app.datasources collect [--tasks public_evidence_capture,post_close_public_archive]
    python -m app.datasources project-valuations --trade-date YYYY-MM-DD [--apply]

``catalog``/``validate`` need nothing but this package.  ``collect`` needs the
PG* environment and runs the collectors under the same durable leases as the
quant service, so it can be deployed as its own container (set
``PUBLIC_EVIDENCE_CAPTURE_ENABLED=false`` / ``POST_CLOSE_PUBLIC_ARCHIVE_ENABLED=false``
on the service, or simply let the lease decide).  Live source probing lives in
``scripts/probe-public-sources.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime

from .catalog import bindings_for, capabilities_of, catalog_document, validate_catalog
from .completeness import completeness_problems


def _catalog(args: argparse.Namespace) -> int:
    if args.capability:
        rows = [binding.__dict__ for binding in bindings_for(args.capability, states=(
            "live_verified", "declared", "dormant", "unsupported"))]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    elif args.source:
        print(json.dumps([binding.__dict__ for binding in capabilities_of(args.source)], ensure_ascii=False, indent=2))
    else:
        print(json.dumps(catalog_document(), ensure_ascii=False, indent=2))
    return 0


def _validate(_args: argparse.Namespace) -> int:
    problems = [*validate_catalog(), *completeness_problems()]
    for problem in problems:
        print(problem)
    print(json.dumps({"problems": len(problems)}))
    return 1 if problems else 0


async def _collect(tasks: list[str]) -> None:
    from ..database import AsyncDatabase, Database  # noqa: PLC0415 - only the collector needs PostgreSQL
    from ..async_market_session_repository import realtime_market_session, sse_calendar_open  # noqa: PLC0415
    from ..fuyao_provider import all_a_snapshot_rows, configured, fetch  # noqa: PLC0415
    from ..http_clients import close_http_clients, start_http_clients  # noqa: PLC0415
    from . import runtime  # noqa: PLC0415

    database = Database()
    database.open()
    async_database = AsyncDatabase(database)
    await async_database.open()
    await start_http_clients()
    try:
        async def session_open(now: datetime) -> bool:
            active, _reason = await realtime_market_session(async_database, None, now)
            return active

        async def trading_day(day: date) -> bool:
            return await sse_calendar_open(async_database, day)

        has_fuyao = configured()
        collector = runtime.build_collector_deps(database, fuyao_fetch=fetch if has_fuyao else None,
                                                 fuyao_snapshot=all_a_snapshot_rows if has_fuyao else None)
        archive = runtime.build_archive_deps(database, collector)
        loops = runtime.collector_loops(collector, archive, session_open=session_open, trading_day=trading_day)
        await asyncio.gather(*(runtime.run_leased(database, label, loops[label]) for label in tasks))
    finally:
        await close_http_clients()
        await async_database.close()
        database.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.datasources")
    commands = parser.add_subparsers(dest="command", required=True)
    catalog = commands.add_parser("catalog")
    catalog.add_argument("--capability", default="")
    catalog.add_argument("--source", default="")
    commands.add_parser("validate")
    collect = commands.add_parser("collect")
    collect.add_argument("--tasks", default="public_evidence_capture,post_close_public_archive")
    projection = commands.add_parser("project-valuations", help="preview dated persisted valuation gaps; --apply writes projections")
    projection.add_argument("--trade-date", required=True, type=date.fromisoformat)
    projection.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "catalog":
        return _catalog(args)
    if args.command == "validate":
        return _validate(args)
    if args.command == "project-valuations":
        from ..database import Database  # noqa: PLC0415 - only this command needs PostgreSQL
        from ..daily_valuation_repository import project_valuations  # noqa: PLC0415
        database = Database()
        database.open()
        try:
            result = project_valuations(database, args.trade_date, apply=args.apply)
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            return 2 if result["status"] in {"partial", "blocked"} else 0
        finally:
            database.close()
    from .runtime import COLLECTOR_TASKS  # noqa: PLC0415
    tasks = [task.strip() for task in args.tasks.split(",") if task.strip()]
    unknown = sorted(set(tasks) - set(COLLECTOR_TASKS))
    if unknown:
        parser.error(f"unknown tasks: {', '.join(unknown)}")
    asyncio.run(_collect(tasks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
