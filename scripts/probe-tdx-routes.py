#!/usr/bin/env python3
"""Probe TDX routes; output a versioned, fail-closed route matrix."""

import argparse
import concurrent.futures
import json
import socket
import struct
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# tdx-owner-probe.sh prefixes EMBEDDED_HOSTS_TEXT and pipes the driver, read from stdin, into the owner container, whose
# working directory holds the image's app package. Run as a file, the app package is the checkout's.
if "EMBEDDED_HOSTS_TEXT" not in globals():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "quant-service"))
from app.datasources.sources.tdx_protocol import TdxClient, market_code  # noqa: E402


COMMANDS = ("quotes", "bars", "bars_1m", "ticks_today", "ticks_hist", "xdxr")


def read_hosts(paths):
    hosts, seen = [], set()
    for path in paths:
        if str(path) == "embedded":
            text = globals().get("EMBEDDED_HOSTS_TEXT", "")
        else:
            text = Path(path).read_text(encoding="utf-8")
        for line in text.splitlines():
            value = line.split("#", 1)[0].strip()
            if not value or ":" not in value:
                continue
            host, port = value.rsplit(":", 1)
            item = (host, int(port))
            if item not in seen:
                seen.add(item)
                hosts.append(item)
    return hosts


def default_hist_date(today=None):
    day = today or (datetime.now(timezone.utc) + timedelta(hours=8)).date()
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _valid_quote(rows, expected):
    if not rows:
        return False, "empty"
    if len(rows) != len(expected):
        return False, f"code_mismatch expected={expected!r} actual={[(r.get('market'), r.get('code')) for r in rows]!r}"
    for row, wanted in zip(rows, expected):
        actual = (row.get("market"), row.get("code"))
        if actual != wanted:
            return False, f"code_mismatch expected={wanted!r} actual={actual!r}"
        if float(row.get("price", 0)) <= 0:
            return False, "nonpositive_price"
    return True, None


def _valid_bars(rows):
    if not rows:
        return False, "empty"
    for row in rows:
        try:
            if min(float(row[k]) for k in ("open", "high", "low", "close")) <= 0:
                return False, "nonpositive_ohlc"
            if float(row["high"]) < float(row["low"]):
                return False, "high_below_low"
            datetime.fromisoformat(str(row["datetime"]))
        except (KeyError, TypeError, ValueError):
            return False, "invalid_bar"
    return True, None


def operation(client, name, hist_date):
    sz = market_code("000001.SZ")
    if name == "quotes":
        return client.quotes([sz, market_code("600519.SH")])
    if name == "bars":
        return client.bars(9, sz[0], sz[1], 0, 5)
    if name == "bars_1m":
        return client.bars(8, sz[0], sz[1], 0, 5)
    if name == "ticks_today":
        return client.ticks(sz[0], sz[1], None, max_requests=2)
    if name == "ticks_hist":
        return client.ticks(sz[0], sz[1], hist_date, max_requests=2)
    if name == "xdxr":
        return client.xdxr(sz[0], sz[1])
    raise ValueError(name)


def probe_host(host, port, timeout, handshake_profile, required, hist_date, client_factory=TdxClient):
    row = {"host": host, "port": port, "connect_ms": None, "profile": handshake_profile, "commands": {}, "usable": False}
    started = time.monotonic()
    try:
        with client_factory(host, port, timeout, handshake_profile=handshake_profile) as client:
            row["connect_ms"] = round((time.monotonic() - started) * 1000, 1)
            for name in required:
                command_started = time.monotonic()
                try:
                    value = operation(client, name, hist_date)
                    valid, error = (_valid_quote(value, [market_code("000001.SZ"), market_code("600519.SH")]) if name == "quotes" else
                                    _valid_bars(value) if name in ("bars", "bars_1m") else
                                    (bool(value), "empty" if not value else None))
                    entry = {"rows": len(value), "usable": valid, "ms": round((time.monotonic() - command_started) * 1000, 1)}
                    if error:
                        entry["error"] = error
                    row["commands"][name] = entry
                except Exception as error:
                    row["commands"][name] = {"rows": 0, "usable": False, "ms": round((time.monotonic() - command_started) * 1000, 1), "error": type(error).__name__}
    except (OSError, socket.timeout, TimeoutError, RuntimeError, struct.error) as error:
        row["connect_ms"] = round((time.monotonic() - started) * 1000, 1)
        row["connect_error"] = type(error).__name__
    row["usable"] = all(row["commands"].get(name, {}).get("usable", False) for name in required)
    return row


def main(argv=None, probe_fn=probe_host):
    parser = argparse.ArgumentParser()
    parser.add_argument("--hosts-file", action="append", type=Path)
    parser.add_argument("--profile", choices=("login_one", "legacy_3"), default="login_one")
    parser.add_argument("--require", default="quotes,bars,ticks_hist")
    parser.add_argument("--min-usable-hosts", type=int, default=1)
    parser.add_argument("--egress", choices=("mac", "owner"), default="mac")
    parser.add_argument("--hist-date", type=date.fromisoformat, default=default_hist_date(),
                        help="交易日；节假日前一个工作日请手动指定")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--threads", type=int, default=12)
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--interval", type=float, default=0.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    required = [name.strip() for name in args.require.split(",") if name.strip()]
    unknown = sorted(set(required) - set(COMMANDS))
    if unknown:
        parser.error("unknown --require: " + ", ".join(unknown))
    paths = args.hosts_file or [Path(__file__).parent / "data/tdx_host_candidates.txt"]
    hosts = read_hosts(paths)
    samples = []
    for sample_index in range(max(1, args.samples)):
        if sample_index and args.interval:
            time.sleep(args.interval)
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(12, max(1, args.threads))) as pool:
            futures = [pool.submit(probe_fn, host, port, args.timeout, args.profile, required, args.hist_date) for host, port in hosts]
            results = [future.result() for future in futures]
        samples.append({"probed_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "results": results})
    payload = {"schema": "tdx-route-matrix-v2", "egress": args.egress, "profile": args.profile, "require": required,
               "timeout_s": args.timeout, "threads": min(12, max(1, args.threads)), "hist_date": args.hist_date.isoformat(), "samples": samples}
    text = json.dumps(payload, ensure_ascii=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    def usable_in_every_sample(predicate):
        return set.intersection(*({(row["host"], row["port"]) for row in sample["results"] if predicate(row)}
                                  for sample in samples))

    counts = {name: len(usable_in_every_sample(lambda row, name=name: row["commands"].get(name, {}).get("usable")))
              for name in required}
    usable = len(usable_in_every_sample(lambda row: row["usable"]))
    print(f"egress={args.egress} profile={args.profile} hist_date={args.hist_date.isoformat()} "
          f"probed_at_utc={samples[-1]['probed_at_utc']} hosts={len(hosts)} samples={len(samples)} usable_hosts={usable} "
          "usable_by_command=" + ",".join(f"{k}:{v}" for k, v in counts.items()), file=sys.stderr)
    print(text, end="")
    return 0 if usable >= args.min_usable_hosts else 2


if __name__ == "__main__":
    raise SystemExit(main())
