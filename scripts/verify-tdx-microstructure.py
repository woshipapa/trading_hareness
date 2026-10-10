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

from app.datasources.sources import tdx_microstructure as micro, tdx_protocol  # noqa: E402


HOSTS = list(tdx_protocol.configured_hosts())[:2]
SYMBOLS = [(0, "000001"), (1, "600519")]


def _last_session(day: date) -> date:
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


REQUIRED = {"volume_profile", "minute_series", "auction", "history_minute_data", "minute_data", "unusual", "top_board"}
required_failures = 0


def _probe(name: str, operation):
    global required_failures
    try:
        value = operation()
        if isinstance(value, dict):
            rows = value.get("profiles", value)
            count = len(rows) if isinstance(rows, (list, tuple)) else sum(
                len(item) for item in value.values() if isinstance(item, list)
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
            with tdx_protocol.TdxClient(host, port, 5.0, handshake_profile="login_one") as client:
                for market, code in SYMBOLS:
                    prefix = f"{code}.{('SZ' if market == 0 else 'SH')}"
                    print(json.dumps({"symbol": prefix}))
                    profile, _ = _probe("volume_profile", lambda: micro.parse_volume_profile(client._exchange(micro.build_volume_profile_request(market, code))))
                    _probe("minute_series", lambda: micro.parse_minute_series(
                        client._exchange(micro.build_minute_series_request(market, code, session))))
                    auction, auction_error = _probe("auction", lambda: micro.parse_auction(client._exchange(micro.build_auction_request(market, code))))
                    history, history_error = _probe("history_minute_data", lambda: micro.parse_history_minute_data(
                        client._exchange(micro.build_history_minute_data_request(market, code, session)), code))
                    _probe("minute_data", lambda: micro.parse_minute_data(client._exchange(micro.build_minute_data_request(market, code)), code))
                    if market == 0:
                        _probe("unusual", lambda: micro.parse_unusual(client._exchange(micro.build_unusual_request(market, 0, 5))))
                        _probe("top_board", lambda: micro.parse_top_board(client._exchange(micro.build_top_board_request(0, 3))))

                    ticks, tick_error = _probe("history_ticks_control", lambda: client.ticks(market, code, session))
                    if history_error or tick_error or history is None or ticks is None:
                        print(json.dumps({"check": "history_minute_volume_vs_ticks", "matched": None,
                                          "reason": "one side unavailable"}))
                    else:
                        minute_lots = sum(int(row.get("volume_lots", 0)) for row in history)
                        tick_lots = sum(int(row.get("volume_lots", 0)) for row in ticks)
                        print(json.dumps({"check": "history_minute_volume_vs_ticks", "minute_lots": minute_lots,
                                          "tick_lots": tick_lots, "matched": minute_lots == tick_lots}))

                    session_ticks, session_tick_error = _probe("session_ticks_for_auction_control", lambda: client.ticks(market, code, session))
                    tick_0925 = next((row for row in (session_ticks or []) if str(row.get("time", ""))[:5] == "09:25"), None)
                    auction_0925 = next((row for row in reversed(auction or []) if str(row.get("time", ""))[:5] <= "09:25"), None)
                    matched = None
                    if auction_0925 is not None and tick_0925 is not None:
                        matched = (abs(float(auction_0925["price"]) - float(tick_0925["price"])) < 1e-3 and
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
