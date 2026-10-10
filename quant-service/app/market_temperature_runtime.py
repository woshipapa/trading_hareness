"""Compute, store and read the daily sentiment temperature (decision 0013).

The post-close stage ``market_temperature`` calls ``refresh``. It recomputes the
series over the last ``LOOKBACK_DAYS`` calendar days in one PostgreSQL pass
(about 13 s on the owner database) and stores the recent readings as
``market_temperature_daily`` (see ``derived_daily_readings``).

The read joins two other series of the same sessions:
- the broad-ETF flow. With it the read marks the research signal
  "冰点资金共振": a cold reading (temperature <= 40) on a day the basket
  turnover ran at least 1.5x its 20-session mean. It is "逆势放量" when the
  index also fell that day.
- the golden / silver finger state, shown as trend context beside the
  marker, never combined with it.

The request path only reads stored rows; it never rescans the bars.

    python -m app.market_temperature_runtime --end 2026-10-09 [--keep 400 --lookback-days 700] [--apply]

Without ``--apply`` it only prints the latest readings. ``--keep 400
--lookback-days 700`` stores the whole history once (a backfill: the bars start
in 2025-01, and the first 60 sessions are warm-up).
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from typing import Any

from . import broad_etf_flow, derived_daily_readings, market_timing
from .derived_daily_readings import CN_TZ
from .market_temperature import BOILING, COMPONENTS, FREEZING, VERSION, temperature_series
from .market_temperature_repository import daily_rows

CAPABILITY = "market_temperature_daily"
LOOKBACK_DAYS = 420          # about 290 sessions: the 250-session window plus warm-up
KEEP_SESSIONS = 30           # readings re-stored each evening; unchanged ones are skipped
COLD_CEILING = 40.0          # 冷 and 冰点

# The 2025-04-03..2026-10-09 read-only backtest behind the marker (decision 0013).
# Shown beside it so nobody mistakes ten days for a law.
MARKER_EVIDENCE = {
    "window": "2025-04-03..2026-10-09", "benchmark": "000001.SH close to close",
    "cold_with_surge": {"days": 10, "up_1d": 0.9, "up_3d": 0.9, "up_5d": 0.9},
    "cold_all": {"days": 131, "up_1d": 0.6, "up_3d": 0.57, "up_5d": 0.6},
    "caveat": "four or five independent episodes; an index rebalance or a new listing can also raise ETF turnover",
}
TIMING_EVIDENCE = {
    "window": "2004-01..2026-10", "rule": "MA5 above MA25, entered once VOL5 > VOL60 (2560, fixed 5/25/60)",
    "max_drawdown": {"buy_and_hold": -0.72, "golden_only": -0.39},
    "caveat": "a trend context: since 2015 it says little about the next few days, and the cold-day marker did best "
              "in silver states, so the two are never combined",
}


def compute(connection: Any, end: date, *, lookback_days: int = LOOKBACK_DAYS) -> list[dict[str, Any]]:
    """Every reading from ``end - lookback_days`` to ``end``; early ones lack a temperature (warm-up)."""
    return temperature_series(daily_rows(connection, end - timedelta(days=lookback_days), end))


def refresh(database: Any, end: date, *, keep: int = KEEP_SESSIONS, apply: bool = True,
            lookback_days: int = LOOKBACK_DAYS) -> dict[str, Any]:
    """Recompute and store the last ``keep`` readings; ``completed`` only when ``end`` itself has one."""
    with database.transaction() as connection:
        readings = compute(connection, end, lookback_days=lookback_days)
    scored = [{**reading, "version": VERSION, "research_only": True, "live_effect": "none"}
              for reading in readings if reading["temperature"] is not None][-keep:]
    counts = derived_daily_readings.store(database, CAPABILITY, scored) if apply else {"stored": 0, "unchanged": 0}
    latest = scored[-1] if scored else None
    status = "completed" if latest and latest["trade_date"] == end.isoformat() else "blocked"
    return {
        "status": status, "trade_date": end.isoformat(), **counts, "readings": len(scored),
        "latest": {key: latest[key] for key in ("trade_date", "temperature", "band")} if latest else None,
        "reason": None if status == "completed" else "no temperature for the session yet: its bars or limits are missing",
        "research_only": True, "live_effect": "none",
    }


def marker(temperature: float | None, ratio: float | None, index_change: float | None) -> dict[str, Any] | None:
    """The research marker for one session, or None."""
    if temperature is None or ratio is None or temperature > COLD_CEILING or ratio < broad_etf_flow.SURGE_RATIO:
        return None
    counter_trend = index_change is not None and index_change < 0
    return {"kind": "cold_with_etf_surge", "label": "逆势放量" if counter_trend else "冰点资金共振",
            "counter_trend": counter_trend}


def daily_series(connection: Any, *, days: int = 120, end: date | None = None) -> dict[str, Any]:
    """The newest stored reading of each session, oldest first, with its flow and marker."""
    last = end or datetime.now(CN_TZ).date()
    first = last - timedelta(days=days * 7 // 5 + 14)
    readings = derived_daily_readings.newest(connection, CAPABILITY, first, last)
    flows = {item["trade_date"]: item for item in derived_daily_readings.newest(
        connection, broad_etf_flow.FLOW_CAPABILITY, first, last)}
    timing = {item["trade_date"]: item for item in derived_daily_readings.newest(
        connection, market_timing.CAPABILITY, first, last)}
    previous_close = None
    for reading in readings:
        close = reading.get("index_close")
        change = (close / previous_close - 1) * 100 if close and previous_close else None
        previous_close = close or previous_close
        flow = flows.get(reading["trade_date"])
        ratio = flow.get("ratio") if flow else None
        reading.update({
            "index_change_pct": round(change, 3) if change is not None else None,
            "etf_flow": {key: flow.get(key) for key in ("ratio", "basket_turnover_cny", "codes", "amount_estimated")}
            if flow else None,
            "marker": marker(reading.get("temperature"), ratio, change),
            "timing": {key: timing[reading["trade_date"]].get(key) for key in ("state", "event")}
            if reading["trade_date"] in timing else None,
        })
    return {
        "version": VERSION, "readings": readings[-days:],
        "thresholds": {"freezing": FREEZING, "boiling": BOILING, "cold_ceiling": COLD_CEILING,
                       "etf_surge_ratio": broad_etf_flow.SURGE_RATIO},
        "components": [{"key": item.key, "label": item.label, "direction": item.direction} for item in COMPONENTS],
        "marker_evidence": MARKER_EVIDENCE, "timing_evidence": TIMING_EVIDENCE,
        "research_only": True, "live_effect": "none",
    }


def main(argv: list[str] | None = None, *, database: Any = None) -> int:
    parser = argparse.ArgumentParser(description="daily sentiment temperature (decision 0013)")
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--keep", type=int, default=KEEP_SESSIONS)
    parser.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if database is None:
        from .database import Database
        database = Database()
        database.open()
    result = refresh(database, args.end, keep=args.keep, apply=args.apply, lookback_days=args.lookback_days)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["CAPABILITY", "COLD_CEILING", "KEEP_SESSIONS", "LOOKBACK_DAYS", "compute", "daily_series", "main", "marker",
           "refresh"]
