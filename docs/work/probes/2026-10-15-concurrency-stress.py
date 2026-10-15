#!/usr/bin/env python3
"""Synthetic, local-only pressure test for quant-service bounded executors.

The action sleeps in a worker thread instead of calling a provider or a
database.  This measures the service's admission/queue/timeout behavior and
the resource cost of each configured boundary without touching production.
Run this in the quant-service image because the host Python does not carry the
service dependencies.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
import os
import resource
import time
from typing import Any, Awaitable, Callable

from app import runtime_executors as runtime


Runner = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class Batch:
    name: str
    offered: int
    duration_seconds: float


def synthetic_io(duration_seconds: float, token: int) -> int:
    """A deterministic blocking action with no external side effect."""
    del token
    time.sleep(duration_seconds)
    return 1


def synthetic_cpu(duration_seconds: float, token: int) -> int:
    """A bounded pure-Python burst to expose GIL/CPU oversubscription."""
    deadline = time.perf_counter() + duration_seconds
    value = token + 1
    while time.perf_counter() < deadline:
        value = (value * 1664525 + 1013904223) & 0xFFFFFFFF
    return value


def rss_mb(value: int) -> float:
    # Linux and macOS both report ru_maxrss in KiB for the environments used
    # here.  Keep the conversion explicit in the evidence rather than reading
    # a host-wide process monitor.
    return round(value / 1024.0, 2)


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return round(ordered[index], 3)


async def run_batch(
    runner: Runner,
    batch: Batch,
    *,
    timeout_seconds: float,
    workload: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    latencies: list[float] = []
    rejected = 0
    timed_out = 0

    async def one(token: int) -> None:
        nonlocal rejected, timed_out
        item_started = time.perf_counter()
        try:
            action = synthetic_io if workload == "io" else synthetic_cpu
            await runner(action, batch.duration_seconds, token, timeout_seconds=timeout_seconds)
        except runtime.ExecutorSaturatedError:
            rejected += 1
        except asyncio.TimeoutError:
            timed_out += 1
        else:
            latencies.append((time.perf_counter() - item_started) * 1000.0)

    await asyncio.gather(*(one(token) for token in range(batch.offered)))
    elapsed = time.perf_counter() - started
    accepted = len(latencies)
    return {
        "name": batch.name,
        "offered": batch.offered,
        "accepted": accepted,
        "rejected": rejected,
        "timed_out": timed_out,
        "duration_ms": round(batch.duration_seconds * 1000.0, 1),
        "wall_ms": round(elapsed * 1000.0, 3),
        "throughput_per_second": round(accepted / elapsed, 3) if elapsed else None,
        "latency_p50_ms": percentile(latencies, 0.50),
        "latency_p95_ms": percentile(latencies, 0.95),
        "latency_p99_ms": percentile(latencies, 0.99),
        "latency_max_ms": round(max(latencies), 3) if latencies else None,
    }


def selected_env() -> dict[str, str | None]:
    names = (
        "AKSHARE_MAX_WORKERS",
        "AKSHARE_MAX_QUEUE",
        "REALTIME_VENDOR_MAX_WORKERS",
        "REALTIME_VENDOR_MAX_QUEUE",
        "QUANT_DB_BLOCKING_MAX_WORKERS",
        "QUANT_DB_BLOCKING_MAX_QUEUE",
        "QUANT_ASYNC_READ_POOL_MAX_SIZE",
        "QUANT_DB_POOL_MAX_SIZE",
        "MARKET_SNAPSHOT_PUBLIC_CONCURRENCY",
        "LONGHU_MINUTE_BATCH_WORKERS",
    )
    return {name: os.getenv(name) for name in names}


async def main(profile: str, workload: str) -> dict[str, Any]:
    before = runtime.runtime_executor_status()
    runners: dict[str, tuple[Runner, dict[str, int]]] = {
        "public_source": (
            runtime.run_akshare_blocking,
            before["public_source"],
        ),
        "realtime_vendor": (
            runtime.run_realtime_vendor_blocking,
            before["realtime_vendor"],
        ),
        "database": (
            runtime.run_database_blocking,
            before["database"],
        ),
    }
    resource_started = resource.getrusage(resource.RUSAGE_SELF)
    wall_started = time.perf_counter()
    all_batches: dict[str, list[dict[str, Any]]] = {}

    for name, (runner, capacity) in runners.items():
        slots = capacity["workers"] + capacity["queue_capacity"]
        offered_counts = (
            capacity["workers"],
            slots,
            slots + capacity["workers"],
        )
        batches: list[dict[str, Any]] = []
        for duration in (0.025, 0.150):
            for index, offered in enumerate(offered_counts):
                batch = Batch(
                    name=(
                        f"{name}:steady" if index == 0 else
                        f"{name}:queue_capacity" if index == 1 else
                        f"{name}:overload"
                    ),
                    offered=offered,
                    duration_seconds=duration,
                )
                # The largest queue in the software ceiling is 64 slots.  A
                # 150ms action therefore completes well below this deadline;
                # a timeout means the boundary has a real scheduling problem.
                timeout_seconds=max(5.0, duration * ((slots // max(1, capacity["workers"])) + 4))
                batches.append(await run_batch(
                    runner, batch, timeout_seconds=timeout_seconds, workload=workload,
                ))
        all_batches[name] = batches

    resource_finished = resource.getrusage(resource.RUSAGE_SELF)
    wall_elapsed = time.perf_counter() - wall_started
    summary: dict[str, Any] = {}
    for name, batches in all_batches.items():
        overload = [row for row in batches if row["name"].endswith(":overload")]
        queue = [row for row in batches if row["name"].endswith(":queue_capacity")]
        summary[name] = {
            "overload_rejected_total": sum(row["rejected"] for row in overload),
            "overload_timeout_total": sum(row["timed_out"] for row in overload),
            "queue_capacity_p95_ms_max": max(
                (row["latency_p95_ms"] or 0.0 for row in queue), default=0.0,
            ),
            "queue_capacity_throughput_max": max(
                (row["throughput_per_second"] or 0.0 for row in queue), default=0.0,
            ),
        }

    return {
        "profile": profile,
        "workload": workload,
        "pid": os.getpid(),
        "cpu_count_visible": os.cpu_count(),
        "selected_env": selected_env(),
        "executor_status_before": before,
        "executor_status_after": runtime.runtime_executor_status(),
        "batches": all_batches,
        "summary": summary,
        "resource": {
            "wall_seconds": round(wall_elapsed, 3),
            "cpu_user_seconds": round(resource_finished.ru_utime - resource_started.ru_utime, 3),
            "cpu_system_seconds": round(resource_finished.ru_stime - resource_started.ru_stime, 3),
            "max_rss_mb": rss_mb(resource_finished.ru_maxrss),
            "mean_cpu_fraction_of_visible_cores": round(
                (resource_finished.ru_utime - resource_started.ru_utime
                 + resource_finished.ru_stime - resource_started.ru_stime)
                / wall_elapsed / max(1, os.cpu_count() or 1),
                4,
            ) if wall_elapsed else None,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--workload", choices=("io", "cpu"), default="io")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    print(json.dumps(asyncio.run(main(args.profile, args.workload)), ensure_ascii=False, sort_keys=True))
