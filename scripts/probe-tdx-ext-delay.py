#!/usr/bin/env python3
"""Measure how far TDX extended-market hosts run behind Tencent, one reading per second.

Once per second for --seconds, every --hosts extended-market host is asked for (27, HZ5017) and (47, IF2610) with
tdx_ex_market and Tencent for hkHSTECH, each reading stamped with the local time it arrived. A series' lag is the
shift, in whole seconds, at which most of its price changes recur among the changes of its reference (positive:
it shows a price later); the number of changes it rests on is reported with it. HZ5017 is compared with Tencent.
IF2610 has no public reference: the app reads neither a Sina nor a Tencent futures quote (Sina's nf_IF2610 appears
only as a manual cross-check in docs/archive/tdx-q-extcodes.md, without field positions), so each later host is
compared with the first host. A delay longer than --max-lag or than the run cannot be measured.

A read that fails is written as an entry with the error's class and message in place of a price, a host that failed
is connected again on its next read, and the run goes on. It ends after --max-consecutive-errors seconds in a row
with a failed read (`stopped` in the output, exit status 1). An exception of another kind ends the run at once; the
readings so far are still written.
"""

import argparse
import http.client
import json
import struct
import sys
import time
import urllib.request
import zlib
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant-service"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.datasources.sources import tdx_ex_market, tdx_protocol  # noqa: E402
import tdx_probe_failures as failures  # noqa: E402 - sibling module, after the path is set

INSTRUMENTS = ((27, "HZ5017"), (47, "IF2610"))
#: Tencent's code for the same instrument; IF2610 has none (see the module docstring).
TENCENT_KEYS = {(27, "HZ5017"): "hkHSTECH"}
DEFAULT_HOSTS = "113.45.175.47:7727,139.9.191.175:7727"
#: What a read raises when the network or a host misbehaves; anything else is a bug and ends the run.
TRANSIENT = (OSError, http.client.HTTPException, tdx_protocol.TdxProtocolError, struct.error, zlib.error, ValueError)


def tencent_source(market, code):
    """The label of Tencent's readings of an instrument, None where Tencent has none."""
    key = TENCENT_KEYS.get((market, code))
    return f"tencent:{key}" if key else None


def read_tencent(key, timeout):
    request = urllib.request.Request(f"https://qt.gtimg.cn/q={key}",
                                     headers={"Referer": "https://gu.qq.com", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        text = tdx_protocol.decode_text(response.read())
    fields = text.partition('"')[2].partition('"')[0].split("~")
    if len(fields) < 31:
        raise ValueError(f"Tencent has no quote for {key}: {text[:80]!r}")
    return {"price": float(fields[3]), "server_time": fields[30]}


def tencent_readings(timeout):
    readings = []
    for (market, code), key in TENCENT_KEYS.items():
        reading = {"source": tencent_source(market, code), "instrument": f"{market}:{code}"}
        try:
            reading.update(read_tencent(key, timeout))
        except TRANSIENT as error:
            reading.update(failures.failure(error))
        reading["at"] = time.time()
        readings.append(reading)
    return readings


class HostReader:
    """One extended-market host: its connection opens on the first read and again after a failed one."""

    def __init__(self, host, port, timeout):
        self.label, self.address = f"{host}:{port}", (host, port, timeout)
        self.connection, self.client = ExitStack(), None

    def read(self):
        readings = []
        try:
            if self.client is None:
                self.client = self.connection.enter_context(tdx_ex_market.TdxExMarketClient(*self.address))
            for market, code in INSTRUMENTS:
                price = self.client.quote(market, code)["price"]
                readings.append({"source": self.label, "instrument": f"{market}:{code}", "price": price,
                                 "at": time.time()})
        except TRANSIENT as error:
            self.close()
            readings.append({"source": self.label, "at": time.time(), **failures.failure(error)})
        return readings

    def close(self):
        self.connection.close()
        self.client = None


def price_series(readings, source, instrument):
    """(arrival time, price) of a source, the price rounded to the two decimals Tencent shows: TDX sends float32."""
    return [(reading["at"], round(reading["price"], 2)) for reading in readings
            if "price" in reading and reading["source"] == source and reading["instrument"] == instrument]


def price_changes(points):
    """(arrival time, price) of every reading whose price differs from the one before it."""
    return [(at, price) for (_, before), (at, price) in zip(points, points[1:]) if price != before]


def lag(points, reference, max_lag):
    """How many seconds ``points`` run behind ``reference`` (negative: ahead): the whole-second difference at which
    most price changes of ``points`` recur, with the same price, among the changes of ``reference``. Equal counts
    go to the shift nearer zero."""
    changes, reference_changes = price_changes(points), price_changes(reference)
    reference_times = {}
    for reference_at, price in reference_changes:
        reference_times.setdefault(price, []).append(reference_at)
    votes = Counter(round(at - reference_at) for at, price in changes for reference_at in reference_times.get(price, ())
                    if abs(at - reference_at) <= max_lag)
    shift, matched = max(votes.items(), key=lambda vote: (vote[1], -abs(vote[0])), default=(None, 0))
    return {"lag_s": shift, "matched_changes": matched, "changes": len(changes),
            "reference_changes": len(reference_changes)}


def lags(readings, labels, max_lag):
    rows = []
    for market, code in INSTRUMENTS:
        instrument = f"{market}:{code}"
        tencent = tencent_source(market, code)
        pairs = [(label, tencent) for label in labels] if tencent else [(label, labels[0]) for label in labels[1:]]
        for label, reference in pairs:
            rows.append({"instrument": instrument, "series": label, "behind": reference,
                         **lag(price_series(readings, label, instrument), price_series(readings, reference, instrument),
                               max_lag)})
    return rows


def hosts_arg(text):
    return tdx_protocol.configured_hosts({"TDX_HQ_HOSTS": text})


def emit(payload, output):
    text = json.dumps(payload, ensure_ascii=True, indent=2) + "\n"
    if output:
        output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hosts", type=hosts_arg, default=DEFAULT_HOSTS, help="host:port,host:port, port included")
    parser.add_argument("--seconds", type=int, default=1800, help="number of readings, one per second")
    parser.add_argument("--max-lag", type=float, default=1200.0, help="largest lag looked for, in seconds")
    parser.add_argument("--max-consecutive-errors", type=int, default=failures.MAX_CONSECUTIVE_ERRORS,
                        help="seconds in a row with a failed read after which the run stops")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--egress", choices=("mac", "owner"), default="mac")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    readers = [HostReader(host, port, args.timeout) for host, port in args.hosts]
    labels = [reader.label for reader in readers]
    readings = []
    payload = {"schema": "tdx-ext-delay-v1", "egress": args.egress,
               "started_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "hosts": labels, "seconds": args.seconds, "max_lag_s": args.max_lag, "clock": "local epoch seconds",
               "references": {f"{market}:{code}": tencent_source(market, code) for market, code in INSTRUMENTS},
               "errors": 0, "readings": readings}
    try:
        began = time.monotonic()
        failed_in_a_row = 0
        for tick in range(args.seconds):
            time.sleep(max(0.0, began + tick - time.monotonic()))
            before = len(readings)
            for reader in readers:
                readings += reader.read()
            readings += tencent_readings(args.timeout)
            fresh = readings[before:]
            failed_in_a_row = failed_in_a_row + 1 if any("error" in reading for reading in fresh) else 0
            if tick == 0:      # lets the run be aborted at once if the codes do not name the same instrument
                for reading in fresh:
                    print(f"first tick: {reading}", file=sys.stderr)
            if failed_in_a_row >= args.max_consecutive_errors:
                payload["stopped"] = f"{failed_in_a_row} seconds in a row with a failed read"
                break
    finally:
        for reader in readers:
            reader.close()
        payload["errors"] = sum("error" in reading for reading in readings)
        payload["lags"] = lags(readings, labels, args.max_lag)
        emit(payload, args.output)
        print(f"{payload['errors']} of {len(readings)} readings failed", file=sys.stderr)
        if "stopped" in payload:
            print(f"stopped: {payload['stopped']}", file=sys.stderr)
        for row in payload["lags"]:
            print(f"{row['instrument']} {row['series']} behind {row['behind']}: {row['lag_s']} s, "
                  f"{row['matched_changes']} of {row['changes']} changes", file=sys.stderr)
    return 1 if "stopped" in payload else 0


if __name__ == "__main__":
    raise SystemExit(main())
