"""Read-only in-container acceptance. Prints no credentials or private addresses."""

import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.request import urlopen

sys.path.insert(0, "/app")

import psycopg

from app.longhu_vendor_source import (
    current_session_minute_rows, direct_access_enabled, intraday_source,
)


def main():
    capacity = int(os.environ.get("INTRADAY_WATCHLIST_MAX_SYMBOLS", "40"))
    assert capacity == 100, "watchlist capacity override was not deployed"
    assert not direct_access_enabled(), "executor must use owner gateway"
    assert os.getenv("QUANT_SHARED_READ_API_KEY"), "shared read key missing"
    with urlopen("http://127.0.0.1:8000/health", timeout=10) as response:
        health = json.load(response)
    with urlopen("http://127.0.0.1:8000/api/v1/intraday/services/status", timeout=20) as response:
        status = json.load(response)
    print(json.dumps({
        "health": health.get("status"), "build": os.getenv("APP_GIT_SHA"),
        "runtime_profile": os.getenv("QUANT_RUNTIME_PROFILE"), "watchlist_capacity": capacity,
        "gateway_credentials_configured": True, "direct_vendor_access": False,
        "services_status_received": bool(status),
    }))
    with psycopg.connect("") as connection:
        count = connection.execute("SELECT count(*) FROM quant.intraday_watchlists WHERE enabled").fetchone()[0]
        leases = connection.execute(
            "SELECT lease_key FROM quant.runtime_leases WHERE expires_at>now() ORDER BY lease_key",
        ).fetchall()
    print(json.dumps({"enabled_watches": count, "active_leases": [r[0] for r in leases]}))
    source = intraday_source()
    started = time.monotonic()
    rows, metadata = source.watch_quotes(["000001.SZ", "600664.SH"], max_symbols=2)
    print(json.dumps({"quote_rows": len(rows), "quote_elapsed_seconds": round(time.monotonic()-started, 3),
                      "quote_status": metadata.get("status"), "transport": metadata.get("transport"),
                      "exchange_dates": sorted({str(r.get("trade_date") or "") for r in rows})}))
    assert len(rows) == 2, "quote smoke sample incomplete"
    minutes = source.stock_minutes("600664.SH")
    try:
        current_session_minute_rows(minutes, observed_at=datetime.now(timezone.utc))
        date_gate = "current_exchange_date"
    except RuntimeError:
        date_gate = "rejected_noncurrent_date"
    print(json.dumps({"minute_rows": len(minutes), "minute_date_gate": date_gate,
                      "minute_exchange_dates": sorted({str(r.get("trade_date") or "") for r in minutes}),
                      "notice": "connectivity is not proof of live-session freshness"}))


if __name__ == "__main__":
    main()
