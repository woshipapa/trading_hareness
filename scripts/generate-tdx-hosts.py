#!/usr/bin/env python3
"""Generate the ranked, research-only TDX host pool from probe JSON."""

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "quant-service/app/datasources/sources/tdx_hosts.py"


def _samples(payload):
    if "samples" in payload:
        return payload["samples"]
    return [{"probed_at_utc": payload.get("date", "unknown"), "results": payload.get("results", [])}]


def select_hosts(payload, limit=20):
    samples = _samples(payload)
    required = set(payload.get("require", ("quotes", "bars", "ticks_hist")))
    by_host = {}
    for sample in samples:
        for row in sample.get("results", []):
            key = (row.get("host"), int(row.get("port", 7709)))
            commands = row.get("commands", {})
            usable = row.get("usable")
            if usable is None:
                usable = all(commands.get(name, {}).get("rows", 0) > 0 for name in required)
            if not usable or row.get("connect_error"):
                continue
            by_host.setdefault(key, []).append(float(row.get("connect_ms") or 999999.0))
    needed = len(samples)
    ranked = [(statistics.median(values), host) for host, values in by_host.items() if len(values) == needed]
    ranked.sort(key=lambda item: (item[0], item[1]))
    selected, subnets = [], {}
    for latency, host in ranked:
        subnet = ".".join(host[0].split(".")[:2])
        if subnets.get(subnet, 0) >= 3:
            continue
        selected.append((host, round(latency, 1)))
        subnets[subnet] = subnets.get(subnet, 0) + 1
        if len(selected) >= limit:
            break
    return selected


def render(payload, selected):
    source = payload.get("source", "probe JSON")
    egress = payload.get("egress", "unknown")
    probed = [sample.get("probed_at_utc") for sample in _samples(payload)]
    lines = [
        '"""Generated TDX host pool; research evidence only."""',
        f"# source={source}", f"# egress={egress}", f"# probed_at_utc={','.join(str(x) for x in probed)}",
        "# profile=login_one; selected hosts are usable in every sample, median latency ranked, /16 <= 3.",
        "HOSTS = (",
    ]
    for (host, port), latency in selected:
        lines.append(f"    ({host!r}, {port}),  # median_connect_ms={latency}")
    lines.extend([")", "LATENCY_MS = {", *[f"    {host!r}: {latency}," for (host, _port), latency in selected], "}", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("matrix", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    payload = json.loads(args.matrix.read_text(encoding="utf-8"))
    if "samples" not in payload:
        print("旧三包矩阵不能用于生成: expected v2 JSON with samples", file=sys.stderr)
        return 2
    selected = select_hosts(payload)
    if not selected:
        print("no host usable in every sample", file=sys.stderr)
        return 2
    rendered = render(payload, selected)
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            print(f"{args.output} is stale", file=sys.stderr)
            return 1
        print(f"tdx host pool is current ({len(selected)} hosts)")
        return 0
    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.output} ({len(selected)} hosts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
