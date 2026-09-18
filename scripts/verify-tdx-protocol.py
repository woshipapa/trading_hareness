#!/usr/bin/env python3
"""Check ``app.datasources.sources.tdx_protocol`` against pytdx on a live host.

pytdx is not a service dependency.  Put an unpacked copy on ``PYTHONPATH``
(``pip download pytdx --no-deps`` plus ``six.py``) inside a throwaway
container, then run from ``quant-service``::

    PYTHONPATH=/tmp/pytdx_src python ../scripts/verify-tdx-protocol.py

Verified (exit non-zero on any mismatch): historical ticks and the
ex-rights/share-capital log, row for row against pytdx, and the tick side
codes against Tencent's B/S prints minute by minute.  Reported only: the
quote and K-line commands, which the public hosts refused on 2026-09-18 --
if they start answering again the output says so, and the catalog binding
can be re-evaluated.  Market data only; no credentials are involved.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

for candidate in (Path.cwd(), Path(__file__).resolve().parents[1] / "quant-service"):
    if (candidate / "app").is_dir():
        sys.path.insert(0, str(candidate))
        break

from app.datasources.derived.tick_flow import parse_tencent_detail  # noqa: E402
from app.datasources.sources import tdx_protocol  # noqa: E402

SYMBOLS = ["000001.SZ", "600519.SH", "300750.SZ", "920819.BJ"]


def _last_session(today: date) -> date:
    day = today - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def main() -> int:
    from pytdx.hq import TdxHq_API  # type: ignore[import-not-found]

    host, port = tdx_protocol.configured_hosts()[0]
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    session = _last_session(today)
    failures = 0
    api = TdxHq_API(raise_exception=True)
    with api.connect(host, port, time_out=8), tdx_protocol.TdxClient(host, port, 8) as ours:
        for symbol in SYMBOLS:
            market, code = tdx_protocol.market_code(symbol)
            mine = ours.ticks(market, code, session)
            reference, start = [], 0
            while True:
                chunk = api.get_history_transaction_data(market, code, start, 2000, int(session.strftime("%Y%m%d"))) or []
                reference = chunk + reference
                if len(chunk) < 2000:
                    break
                start += 2000
            bad = abs(len(mine) - len(reference)) + sum(
                1 for left, right in zip(mine, reference)
                if left["time"] != right["time"] or abs(left["price"] - right["price"]) > 1e-9
                or left["volume_lots"] != right["vol"] or left["side_code"] != right["buyorsell"])
            failures += bool(bad)
            print(json.dumps({"check": "history_ticks", "symbol": symbol, "date": session.isoformat(),
                              "rows": len(mine), "mismatches": bad}))

            mine_x, reference_x = ours.xdxr(market, code), api.get_xdxr_info(market, code) or []
            bad = abs(len(mine_x) - len(reference_x))
            for left, right in zip(mine_x, reference_x):
                if left["category"] != right["category"]:
                    bad += 1
                elif left["category"] == 1 and abs(left["cash_dividend_per_10"] - right["fenhong"]) > 1e-6:
                    bad += 1
                elif left["category"] not in (1, 11, 12, 13, 14) and abs(left["float_shares_after_10k"] - right["panhouliutong"]) > 1e-6:
                    bad += 1
            failures += bool(bad)
            print(json.dumps({"check": "xdxr", "symbol": symbol, "rows": len(mine_x), "mismatches": bad}))

        # Side codes: TDX 0/1 against Tencent B/S, per minute, for today.
        if today.weekday() < 5:
            tdx_rows = ours.ticks(0, "000001", today)
            tencent = []
            for page in range(150):
                request = urllib.request.Request(
                    f"http://stock.gtimg.cn/data/index.php?appn=detail&action=data&c=sz000001&p={page}",
                    headers={"User-Agent": "Mozilla/5.0"})
                rows = parse_tencent_detail(urllib.request.urlopen(request, timeout=10).read().decode("gbk", "ignore"))
                if not rows:
                    break
                tencent.extend(rows)
            per_minute: dict[tuple[str, str], list[float]] = {}
            for row in tdx_rows:
                side = {0: "B", 1: "S"}.get(row["side_code"])
                if side:
                    per_minute.setdefault((row["time"], side), [0.0, 0.0])[0] += row["volume_lots"]
            for tick in tencent:
                if tick.side in {"B", "S"}:
                    per_minute.setdefault((tick.time[:5], tick.side), [0.0, 0.0])[1] += tick.volume_shares / 100
            overlap = sum(min(a, b) for a, b in per_minute.values())
            total = sum(max(a, b) for a, b in per_minute.values())
            agreement = overlap / total if total else None
            failures += bool(agreement is not None and agreement < 0.95)
            print(json.dumps({"check": "side_codes_vs_tencent", "tdx_rows": len(tdx_rows), "tencent_rows": len(tencent),
                              "volume_agreement": round(agreement, 4) if agreement is not None else None}))

        stocks = [tdx_protocol.market_code(symbol) for symbol in SYMBOLS]
        try:
            quotes = ours.quotes(stocks)
        except Exception as error:  # noqa: BLE001 - informational
            quotes = f"{type(error).__name__}"
        try:
            bars = ours.bars(9, *stocks[0], 0, 5)
        except Exception as error:  # noqa: BLE001 - informational
            bars = f"{type(error).__name__}"
        print(json.dumps({"check": "closed_commands (informational)",
                          "quotes": len(quotes) if isinstance(quotes, list) else quotes,
                          "daily_bars": len(bars) if isinstance(bars, list) else bars}))
    print(json.dumps({"summary": "ok" if not failures else "mismatch", "failures": failures, "host": f"{host}:{port}"}))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
