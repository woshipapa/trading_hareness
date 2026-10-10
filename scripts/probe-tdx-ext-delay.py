#!/usr/bin/env python3
"""Measure how far TDX extended-market hosts run behind Tencent, one reading per second.

Once per second for --seconds, every --hosts extended-market host is asked for (27, HZ5017) and (47, IF2610) with
tdx_ex_market and Tencent for hkHSTECH, each reading stamped with the local time it arrived. A series' lag is the
shift, in whole seconds, at which most of its price changes recur among the changes of its reference (positive:
it shows a price later); the number of changes it rests on is reported with it. HZ5017 is compared with Tencent.
IF2610 has no public reference: the app reads neither a Sina nor a Tencent futures quote (Sina's nf_IF2610 appears
only as a manual cross-check in docs/archive/tdx-q-extcodes.md, without field positions), so each later host is
compared with the first host. A delay longer than --max-lag or than the run cannot be measured. An error stops the
run and the readings so far are still written.
"""

import argparse
import json
import sys
import time
import urllib.request
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant-service"))
from app.datasources.sources import tdx_ex_market, tdx_protocol  # noqa: E402

INSTRUMENTS = ((27, "HZ5017"), (47, "IF2610"))
#: Tencent's code for the same instrument; IF2610 has none (see the module docstring).
TENCENT_KEYS = {(27, "HZ5017"): "hkHSTECH"}
DEFAULT_HOSTS = "113.45.175.47:7727,139.9.191.175:7727"


def read_tencent(key, timeout):
    request = urllib.request.Request(f"https://qt.gtimg.cn/q={key}",
                                     headers={"Referer": "https://gu.qq.com", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        text = tdx_protocol.decode_text(response.read())
    fields = text.partition('"')[2].partition('"')[0].split("~")
    if len(fields) < 31:
        raise ValueError(f"Tencent has no quote for {key}: {text[:80]!r}")
    return {"price": float(fields[3]), "server_time": fields[30]}


def read_host(label, client):
    readings = []
    for market, code in INSTRUMENTS:
        price = client.quote(market, code)["price"]
        readings.append({"source": label, "instrument": f"{market}:{code}", "price": price, "at": time.time()})
    return readings


def price_series(readings, source, instrument):
    """(arrival time, price) of a source, the price rounded to the two decimals Tencent shows: TDX sends float32."""
    return [(reading["at"], round(reading["price"], 2))
            for reading in readings if reading["source"] == source and reading["instrument"] == instrument]


def price_changes(points):
    """(arrival time, price) of every reading whose price differs from the one before it."""
    return [(at, price) for (_, before), (at, price) in zip(points, points[1:]) if price != before]


def lag(points, reference, max_lag):
    """How many seconds ``points`` run behind ``reference`` (negative: ahead): the whole-second difference at which
    most price changes of ``points`` recur, with the same price, among the changes of ``reference``. Equal counts
    go to the shift nearer zero."""
    changes, reference_changes = price_changes(points), price_changes(reference)
    votes = Counter(round(at - reference_at) for at, price in changes for reference_at, reference_price in reference_changes
                    if price == reference_price and abs(at - reference_at) <= max_lag)
    shift, matched = max(votes.items(), key=lambda vote: (vote[1], -abs(vote[0])), default=(None, 0))
    return {"lag_s": shift, "matched_changes": matched, "changes": len(changes),
            "reference_changes": len(reference_changes)}


def tencent_source(market, code):
    """The label of Tencent's readings of an instrument, None where Tencent has none."""
    key = TENCENT_KEYS.get((market, code))
    return f"tencent:{key}" if key else None


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
    parser.add_argument("--seconds", type=int, default=600, help="number of readings, one per second")
    parser.add_argument("--max-lag", type=float, default=300.0, help="largest lag looked for, in seconds")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--egress", choices=("mac", "owner"), default="mac")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    labels = [f"{host}:{port}" for host, port in args.hosts]
    readings = []
    payload = {"schema": "tdx-ext-delay-v1", "egress": args.egress,
               "started_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "hosts": labels, "seconds": args.seconds, "max_lag_s": args.max_lag, "clock": "local epoch seconds",
               "references": {f"{market}:{code}": tencent_source(market, code) for market, code in INSTRUMENTS},
               "readings": readings}
    try:
        with ExitStack() as clients:
            connections = [clients.enter_context(tdx_ex_market.TdxExMarketClient(host, port, args.timeout))
                           for host, port in args.hosts]
            began = time.monotonic()
            for tick in range(args.seconds):
                time.sleep(max(0.0, began + tick - time.monotonic()))
                for label, client in zip(labels, connections):
                    readings += read_host(label, client)
                for (market, code), key in TENCENT_KEYS.items():
                    quote = read_tencent(key, args.timeout)
                    readings.append({"source": tencent_source(market, code), "instrument": f"{market}:{code}", **quote,
                                     "at": time.time()})
                if tick == 0:      # lets the run be aborted at once if the codes do not name the same instrument
                    for reading in readings:
                        print(f"first reading {reading['source']} {reading['instrument']}: {reading['price']}", file=sys.stderr)
    finally:
        payload["lags"] = lags(readings, labels, args.max_lag)
        emit(payload, args.output)
        for row in payload["lags"]:
            print(f"{row['instrument']} {row['series']} behind {row['behind']}: {row['lag_s']} s, "
                  f"{row['matched_changes']} of {row['changes']} changes", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
