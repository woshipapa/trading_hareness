#!/usr/bin/env python3
"""Data-free live check of every token-free public evidence source.

Prints one JSON line per source (status, row count, latency, first symbol)
and a summary; never prints payload text or credentials.  Run inside the
quant container (``/app`` importable) on the host whose egress IP you want to
test -- Eastmoney throttles per IP, so a Mac result says nothing about the peer::

    docker exec trading-hareness-peer-quant-research-scheduler-1 python /tmp/probe.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

for candidate in (Path("/app"), Path.cwd(), Path(__file__).resolve().parents[1] / "quant-service"):
    if (candidate / "app").is_dir():
        sys.path.insert(0, str(candidate))
        break

from app.datasources.sources import (  # noqa: E402
    eastmoney_datacenter, eastmoney_hot_rank, eastmoney_ztb as eastmoney_limit_pools,
    investor_qa as investor_qa_providers, news_flash as news_flash_providers, ticks as tick_sources,
    ttfund as ttfund_nav,
)


def last_weekday(today: date) -> date:
    day = today - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


async def main() -> int:
    now = datetime.now(timezone.utc)
    today = now.date()
    session = last_weekday(today + timedelta(days=1)) if today.weekday() < 5 else last_weekday(today)
    checks = {
        **{f"eastmoney_ztb:{pool}": (lambda pool=pool: eastmoney_limit_pools.fetch_pool(pool, session))
           for pool in eastmoney_limit_pools.POOLS},
        "eastmoney_ztb:stock_changes": lambda: eastmoney_limit_pools.fetch_stock_changes(),
        "eastmoney_ztb:board_changes": lambda: eastmoney_limit_pools.fetch_board_changes(50),
        "eastmoney_hot_rank:popularity": lambda: eastmoney_hot_rank.fetch_rank_list("popularity"),
        "eastmoney_hot_rank:surge": lambda: eastmoney_hot_rank.fetch_rank_list("surge"),
        "eastmoney_hot_rank:history": lambda: eastmoney_hot_rank.fetch_rank_history("000001.SZ"),
        **{f"eastmoney_datacenter:{key}": (lambda key=key: eastmoney_datacenter.fetch_report(
            key, start=eastmoney_datacenter.default_window(key, today)[0],
            end=eastmoney_datacenter.default_window(key, today)[1], max_pages=1))
           for key in ("restricted_release", "holder_count", "holder_trade", "block_trade", "earnings_forecast",
                       "earnings_express", "repurchase", "ipo_calendar", "margin_market")},
        **{f"{provider}:news_flash": fetch for provider, fetch in news_flash_providers.FETCHERS.items()},
        "cninfo_irm:investor_qa": lambda: investor_qa_providers.fetch_cninfo(page_size=10),
        "sse_einteract:investor_qa": lambda: investor_qa_providers.fetch_sse(now, page_size=10),
        "tdx_public:history_ticks": lambda: tick_sources.fetch_tdx_ticks("000001.SZ", last_weekday(today)),
        "tdx_public:capital_changes": lambda: tick_sources.fetch_tdx_capital_changes("000001.SZ"),
        "tencent_free:ticks_today": lambda: tick_sources.fetch_tencent_ticks("000001.SZ", max_pages=2),
        "ttfund:nav": lambda: ttfund_nav.fetch_nav_history("161725", page_size=5),
    }
    counts: dict[str, int] = {}
    for name, check in checks.items():
        started = time.monotonic()
        try:
            result = await check()
            rows = result[0] if isinstance(result, tuple) else result
            state = "success" if rows else "valid_empty"
            first = rows[0] if rows else None
            symbol = (first.get("symbol") or first.get("ts_code")) if isinstance(first, dict) else None
            record = {"source": name, "state": state, "rows": len(rows), "first_symbol": symbol}
        except Exception as error:  # noqa: BLE001
            state = "failed"
            record = {"source": name, "state": state, "error": f"{type(error).__name__}: {str(error)[:160]}"}
        record["ms"] = int((time.monotonic() - started) * 1000)
        counts[state] = counts.get(state, 0) + 1
        print(json.dumps(record, ensure_ascii=False), flush=True)
        await asyncio.sleep(0.2)
    print(json.dumps({"summary": counts, "count": len(checks)}))
    return 1 if counts.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
