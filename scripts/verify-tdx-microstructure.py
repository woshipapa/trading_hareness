#!/usr/bin/env python3
"""Read-only probes for the legacy TDX microstructure command family.

Each host is opened once and all commands are issued over that connection.  A
failure is reported per command so old tick-only hosts remain useful controls.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

for candidate in (Path.cwd(), Path(__file__).resolve().parents[1] / "quant-service"):
    if (candidate / "app").is_dir():
        sys.path.insert(0, str(candidate))
        break

from app.datasources.sources import tdx_microstructure  # noqa: E402


HOSTS = [("117.34.114.13", 7709), ("60.191.117.167", 7709)]
SYMBOLS = [(0, "000001"), (1, "600519")]


def _last_session(day: date) -> date:
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


REQUIRED = {"volume_profile", "history_orders", "auction", "history_minute_data", "minute_data"}
required_failures = 0


def _probe(name: str, operation):
    global required_failures
    try:
        value = operation()
        if isinstance(value, dict):
            rows = value.get("profiles", value)
            count = len(rows) if isinstance(rows, (list, tuple)) else sum(
                len(item) for key, item in value.items() if key != "size" and isinstance(item, list)
            )
        else:
            count = len(value) if isinstance(value, (list, tuple)) else 1
        sample = value[:3] if isinstance(value, list) else value.get("profiles", [])[:3] if isinstance(value, dict) else value
        print(json.dumps({"command": name, "rows": count, "sample": sample}, ensure_ascii=False, default=str))
        if name in REQUIRED and count == 0:
            required_failures += 1
        return value, None
    except Exception as error:  # noqa: BLE001 - a per-command probe must continue
        print(json.dumps({"command": name, "rows": 0, "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        if name in REQUIRED:
            required_failures += 1
        return None, error


def main() -> int:
    global required_failures
    session = _last_session(datetime.now(ZoneInfo("Asia/Shanghai")).date())
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    print(json.dumps({"session": session.isoformat(), "today": today.isoformat(), "hosts": HOSTS}))
    for host, port in HOSTS:
        print(json.dumps({"host": f"{host}:{port}"}))
        try:
            with tdx_microstructure.TdxMicrostructureClient(host, port, 5.0) as client:
                for market, code in SYMBOLS:
                    prefix = f"{code}.{('SZ' if market == 0 else 'SH')}"
                    print(json.dumps({"symbol": prefix}))
                    profile, _ = _probe("volume_profile", lambda: client.volume_profile(market, code))
                    _probe("history_orders", lambda: client.history_orders(market, code, session))
                    auction, _ = _probe("auction", lambda: client.auction(market, code))
                    _probe("history_minute_data", lambda: client.history_minute_data(market, code, session))
                    _probe("minute_data", lambda: client.minute_data(market, code))
                    if market == 0:
                        _probe("unusual", lambda: client.unusual(market, 0, 5))
                        _probe("top_board", lambda: client.top_board(0, 3))

                    history, history_error = _probe("history_minute_data_check", lambda: client.history_minute_data(market, code, session))
                    ticks, tick_error = _probe("history_ticks_control", lambda: client.ticks(market, code, session))
                    if history_error or tick_error or history is None or ticks is None:
                        print(json.dumps({"check": "history_minute_volume_vs_ticks", "matched": None,
                                          "reason": "one side unavailable"}))
                    else:
                        minute_lots = sum(int(row.get("volume_lots", 0)) for row in history)
                        tick_lots = sum(int(row.get("volume_lots", 0)) for row in ticks)
                        print(json.dumps({"check": "history_minute_volume_vs_ticks", "minute_lots": minute_lots,
                                          "tick_lots": tick_lots, "matched": minute_lots == tick_lots}))

                    auction_today, auction_error = _probe("auction_today", lambda: client.auction(market, code))
                    session_ticks, session_tick_error = _probe("session_ticks_for_auction_control", lambda: client.ticks(market, code, session))
                    tick_0925 = next((row for row in (session_ticks or []) if str(row.get("time", ""))[:5] == "09:25"), None)
                    auction_0925 = next((row for row in reversed(auction_today or []) if str(row.get("time", ""))[:5] <= "09:25"), None)
                    matched = None
                    if auction_0925 is not None and tick_0925 is not None:
                        matched = (abs(float(auction_0925["price"]) - float(tick_0925["price"])) < 1e-6 and
                                   int(auction_0925["matched_raw"]) == int(tick_0925.get("volume_lots", 0)) * 100)
                    print(json.dumps({"check": "auction_0925_vs_ticks", "auction": auction_0925,
                                      "tick": tick_0925, "matched": matched,
                                      "errors": [type(error).__name__ for error in (auction_error, session_tick_error) if error]}))
        except Exception as error:  # noqa: BLE001 - connection failure is a host result
            required_failures += len(REQUIRED)
            print(json.dumps({"host": f"{host}:{port}", "connection_error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
    return 1 if required_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
