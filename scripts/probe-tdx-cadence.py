#!/usr/bin/env python3
"""Probe TDX request rates: failure rate, latency and data change per host at fixed requests per second.

For each request kind (legacy 0x054b all-A page of 80, MAC 0x122b batch of 80 symbols on the MAC hosts, legacy
0x053e quote of 80 symbols) and host, every rate of --rates runs for --seconds on one kept-open connection, then
once more with a new connection per request. Requests are sequential: one never starts sooner than 1/rate after the
one before it, and a late one is not made up for with a burst. The 80 symbols are those of the first all-A page by
code. Per run: the failure rate, the p50/p95 latency of the answered requests and how often the digest of the rows
changed between answers (a host that serves a stale cache never changes). Read-only; rates above 10/s are refused.
"""

import argparse
import hashlib
import json
import math
import struct
import sys
import time
from collections import Counter
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant-service"))
from app.datasources.sources import tdx_legacy_misc, tdx_mac, tdx_protocol  # noqa: E402
from app.datasources.sources.tdx_hosts import HOSTS  # noqa: E402

MAX_RATE = 10
#: What a host can do to one request: no answer, a closed socket, bytes that do not decode.
WIRE_ERRORS = (OSError, tdx_protocol.TdxProtocolError, tdx_mac.TdxMacError, struct.error, IndexError, ValueError)


def all_a_page(client):
    request = tdx_legacy_misc.build_quotes_list_request(
        tdx_legacy_misc.QUOTE_CATEGORIES["all_a"], tdx_legacy_misc.QUOTE_SORT_TYPES["code"])
    return tdx_legacy_misc.parse_quotes_list(client._exchange(request))[0]


def kinds(stocks, legacy_hosts):
    """(name, hosts, client class, request) of the three request kinds."""
    return (
        ("legacy_0x054b_all_a_page", legacy_hosts, tdx_protocol.TdxClient, all_a_page),
        ("mac_0x122b_batch", tdx_mac.configured_hosts(), tdx_mac.TdxMacClient,
         lambda client: client.batch_quotes(stocks)),
        ("legacy_0x053e_quote", legacy_hosts, tdx_protocol.TdxClient, lambda client: client.quotes(stocks)),
    )


def due_times(rate, seconds):
    """Start offsets of the requests of one run, each at least 1/rate after the one before. A slot is used only if
    it begins half an interval before the end, so float noise cannot add a request."""
    interval = 1 / rate
    began = time.monotonic()
    due = began
    while due < began + seconds - interval / 2:
        time.sleep(max(0.0, due - time.monotonic()))
        yield time.monotonic() - began
        due = max(due + interval, time.monotonic())


@contextmanager
def timed(record, key):
    began = time.monotonic()
    try:
        yield
    finally:
        record[key] = round((time.monotonic() - began) * 1000, 1)


def digest(rows):
    return hashlib.sha1(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()[:12]


def percentile(values, q):
    """Nearest-rank percentile; None without values."""
    if not values:
        return None
    return sorted(values)[math.ceil(q * len(values) / 100) - 1]


def measure(connect, request, rate, seconds, *, keep_open):
    """One run: a record per request. ``connect_ms`` is set on a request that opened its connection, ``ms`` is
    the exchange itself; a failed request carries the error class instead of a digest."""
    records = []
    with ExitStack() as connection:
        client = None
        for start in due_times(rate, seconds):
            record = {"at": round(start, 3)}
            try:
                if client is None:
                    with timed(record, "connect_ms"):
                        client = connection.enter_context(connect())
                with timed(record, "ms"):
                    rows = request(client)
                record["digest"] = digest(rows)
            except WIRE_ERRORS as error:
                record["error"] = type(error).__name__
            if "error" in record or not keep_open:
                connection.close()
                client = None
            records.append(record)
    return records


def summarize(records, seconds):
    answered = [record for record in records if "error" not in record]
    digests = [record["digest"] for record in answered]
    latencies = [record["ms"] for record in answered]
    connects = [record["connect_ms"] for record in answered if "connect_ms" in record]
    return {
        "requests": len(records),
        "failures": len(records) - len(answered),
        "failure_rate": round(1 - len(answered) / len(records), 4),
        "errors": dict(Counter(record["error"] for record in records if "error" in record)),
        "achieved_rate": round(len(records) / seconds, 2),
        "p50_ms": percentile(latencies, 50),
        "p95_ms": percentile(latencies, 95),
        "connect_p50_ms": percentile(connects, 50),
        "connect_p95_ms": percentile(connects, 95),
        "digest_changes": sum(before != after for before, after in zip(digests, digests[1:])),
        "distinct_digests": len(set(digests)),
    }


def hosts_arg(text):
    return tdx_protocol.configured_hosts({"TDX_HQ_HOSTS": text})


def rates_arg(text):
    return [float(item) for item in text.split(",")]


def emit(payload, output):
    text = json.dumps(payload, ensure_ascii=True, indent=2) + "\n"
    if output:
        output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hosts", type=hosts_arg,
                        help="host:port,host:port for the two legacy kinds (default: the first --host-count of "
                             "tdx_hosts); the MAC kind always runs on the MAC hosts")
    parser.add_argument("--host-count", type=int, default=3)
    parser.add_argument("--rates", type=rates_arg, default="1,2,5", help="requests per second, comma-separated")
    parser.add_argument("--seconds", type=float, default=10.0, help="length of each run")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--egress", choices=("mac", "owner"), default="mac")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not all(0 < rate <= MAX_RATE and rate * args.seconds >= 1 for rate in args.rates):
        parser.error(f"every rate must be above 0 and at most {MAX_RATE} per second, and send a request within --seconds")
    legacy_hosts = args.hosts or HOSTS[:args.host_count]
    stocks = [(row["market"], row["code"]) for row in tdx_protocol.call_sync(
        all_a_page, hosts=legacy_hosts, timeout_seconds=args.timeout)[0]]
    runs = []
    payload = {"schema": "tdx-cadence-v1", "egress": args.egress,
               "started_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "seconds": args.seconds, "rates": args.rates, "timeout_s": args.timeout,
               "symbols": [tdx_protocol.symbol(*stock) for stock in stocks], "runs": runs}
    try:
        for name, hosts, client_class, request in kinds(stocks, legacy_hosts):
            for host, port in hosts:
                for keep_open in (True, False):
                    for rate in args.rates:
                        records = measure(lambda: client_class(host, port, args.timeout), request, rate,
                                          args.seconds, keep_open=keep_open)
                        summary = summarize(records, args.seconds)
                        connection = "kept_open" if keep_open else "new_per_request"
                        runs.append({"kind": name, "host": f"{host}:{port}", "connection": connection,
                                     "rate": rate, "summary": summary, "requests": records})
                        print(f"{name} {host}:{port} {connection} {rate:g}/s: {summary['requests']} requests, "
                              f"{summary['failures']} failed, p50 {summary['p50_ms']} ms, p95 {summary['p95_ms']} ms, "
                              f"data changed {summary['digest_changes']} times", file=sys.stderr)
    finally:
        emit(payload, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
