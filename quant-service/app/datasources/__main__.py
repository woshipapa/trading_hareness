"""Standalone entry point for the data-source layer.

    python -m app.datasources catalog [--capability limits.limit_up_pool] [--source fuyao_ths]
    python -m app.datasources validate
    python -m app.datasources collect [--tasks public_evidence_capture,post_close_public_archive]
    python -m app.datasources project-valuations --trade-date YYYY-MM-DD [--apply]
    python -m app.datasources probe <source> <capability> [--params '{"symbol": "999999.SH"}']

``catalog``/``validate`` need nothing but this package.  ``collect`` needs the
PG* environment and runs the collectors under the same durable leases as the
quant service, so it can be deployed as its own container (set
``PUBLIC_EVIDENCE_CAPTURE_ENABLED=false`` / ``POST_CLOSE_PUBLIC_ARCHIVE_ENABLED=false``
on the service, or simply let the lease decide).  Live source probing lives in
``scripts/probe-public-sources.py``.

``probe`` calls the adapter the catalog names for one binding, whatever the
binding's status, once, with ``--params`` (a JSON object) as its keyword
arguments, and prints one JSON object: what was asked, when, how many rows,
coverage, the effective and available ranges, the warnings (they carry the
answering host), the first rows and the error if the adapter raised (exit 1).
It writes nothing.  ``scripts/tdx-promote.py`` runs it on the Mac or inside the
owner container and records the answer as promotion evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import importlib
import json
import sys
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime, timezone
from typing import Any

from .catalog import bindings_for, capabilities_of, catalog_document, validate_catalog
from .completeness import completeness_problems
from .contracts import BINDING_STATES, Binding
from .resolver import _unpack_evidence

#: Rows of an adapter's answer that ``probe`` prints.
SAMPLE_ROWS = 5


def _jsonable(value: object) -> object:
    """``json.dumps`` default for a binding's BindingSpec, its mappings, Decimal factors and timestamps."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _catalog(args: argparse.Namespace) -> int:
    if args.capability:
        rows = [binding.__dict__ for binding in bindings_for(args.capability, states=(
            "live_verified", "declared", "dormant", "unsupported"))]
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=_jsonable))
    elif args.source:
        print(json.dumps([binding.__dict__ for binding in capabilities_of(args.source)], ensure_ascii=False, indent=2,
                         default=_jsonable))
    else:
        print(json.dumps(catalog_document(), ensure_ascii=False, indent=2))
    return 0


def _validate(_args: argparse.Namespace) -> int:
    problems = [*validate_catalog(), *completeness_problems()]
    for problem in problems:
        print(problem)
    print(json.dumps({"problems": len(problems)}))
    return 1 if problems else 0


def _adapter(binding: Binding) -> Callable[..., Awaitable[object]]:
    """The function the catalog names for ``binding`` (``app/<module path>.py:<function>``)."""
    path, _, name = binding.adapter.partition(":")
    return getattr(importlib.import_module(path.removesuffix(".py").replace("/", ".")), name)


async def _call(binding: Binding, params: dict[str, Any]) -> dict[str, Any]:
    """Call the binding's adapter once and describe the answer; an adapter that raises is reported, not hidden."""
    record: dict[str, Any] = {"source": binding.source, "capability": binding.capability, "params": params,
                              "started_utc": datetime.now(timezone.utc).isoformat()}
    try:
        rows, evidence = _unpack_evidence(await _adapter(binding)(**params))
    except Exception as error:  # noqa: BLE001 - the probe's answer is whatever the adapter raised
        record["error"] = f"{type(error).__name__}: {error}"
    else:
        record |= {"rows": len(rows), "coverage": evidence.coverage,
                   "effective_at_min": evidence.effective_at_min, "effective_at_max": evidence.effective_at_max,
                   "available_at_min": evidence.available_at_min, "available_at_max": evidence.available_at_max,
                   "warnings": list(evidence.warnings), "sample": rows[:SAMPLE_ROWS], "error": None}
    record["finished_utc"] = datetime.now(timezone.utc).isoformat()
    return record


def _probe(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    binding = next((item for item in bindings_for(args.capability, states=BINDING_STATES)
                    if item.source == args.source), None)
    if binding is None or ":" not in (binding.adapter or ""):
        parser.error(f"{args.source} -> {args.capability}: no such binding, or its adapter names no function")
    record = asyncio.run(_call(binding, args.params))
    print(json.dumps(record, ensure_ascii=False, indent=2, default=_jsonable))
    return 1 if record["error"] else 0


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
    probe = commands.add_parser("probe", help="call one binding's adapter once and print the answer; writes nothing")
    probe.add_argument("source")
    probe.add_argument("capability")
    probe.add_argument("--params", type=json.loads, default={}, help="JSON object: the adapter's keyword arguments")
    args = parser.parse_args(argv)
    if args.command == "catalog":
        return _catalog(args)
    if args.command == "validate":
        return _validate(args)
    if args.command == "probe":
        return _probe(args, parser)
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
