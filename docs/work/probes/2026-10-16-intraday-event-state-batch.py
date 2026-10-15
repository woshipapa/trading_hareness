"""Synthetic statement-count probe for one 200-symbol realtime scan.

This calls the repository functions with a no-row connection.  It measures
database round trips only; it does not connect to owner PostgreSQL or a market
provider.
"""

from datetime import datetime, timezone
import json
import sys

sys.path.insert(0, "quant-service")

from app.intraday_scan_repository import (  # noqa: E402
    load_intraday_signal_event_state,
    load_intraday_signal_event_states,
)


class _Result:
    def fetchall(self):
        return []

    def fetchone(self):
        return None


class _Connection:
    def __init__(self):
        self.calls = 0

    def execute(self, _sql, _params=None):
        self.calls += 1
        return _Result()


def main() -> None:
    symbols = [f"{index:06d}.SZ" for index in range(1, 201)]
    keys = {symbol: [f"{symbol}:watch:volume_anomaly"] for symbol in symbols}
    session_start = datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc)

    per_symbol = _Connection()
    for symbol in symbols:
        load_intraday_signal_event_state(per_symbol, keys[symbol], symbol, session_start=session_start)

    basket = _Connection()
    load_intraday_signal_event_states(basket, keys, session_start=session_start)

    print(json.dumps({
        "symbols": len(symbols),
        "per_symbol_queries": per_symbol.calls,
        "basket_queries": basket.calls,
        "query_reduction_factor": round(per_symbol.calls / basket.calls, 1),
        "scope": "synthetic repository statement count; no owner DB/provider access",
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
