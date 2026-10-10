"""Golden / silver finger: the 2560 market-timing state of the SSE composite (decision 0013).

The state is V2 of the 2026-10-10 backtest:
- it turns golden (金指) when MA5 is above MA25 and VOL5 > VOL60 confirms;
- it turns silver (银指) when MA5 falls below MA25.

The parameters are 2560's fixed 5 / 25 / 60.

Over 2004-2026 the state cut the SSE drawdown from -72% to -39%. Since 2015
it has said little about the next few days, so it is a trend context and
never a signal on its own. Nor is it combined with the cold-day ETF-surge
marker: that marker did best in silver states.

The post-close stage ``market_timing`` does three things:
- fetches about 270 sessions of the index from one source: Fuyao, or Tencent
  for the whole window when Fuyao fails, since two volume units must not
  mix in VOL5/VOL60;
- replays the state across the window;
- stores the last ``KEEP_SESSIONS`` readings as ``market_timing_daily``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from statistics import fmean
from typing import Any

from . import derived_daily_readings
from .derived_daily_readings import CN_TZ, session_close

INDEX_CODE = "000001.SH"
CAPABILITY = "market_timing_daily"
VERSION = "golden-finger-2560-v2"
SHORT, LONG, VOLUME_SHORT, VOLUME_LONG = 5, 25, 5, 60
FETCH_DAYS = 400             # about 270 sessions: MA60 warm-up plus a replay long enough to forget the start
KEEP_SESSIONS = 30
TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


async def fuyao_index_bars(start: date, end: date) -> list[dict[str, Any]]:
    from .fuyao_provider import fetch_envelope
    envelope = await fetch_envelope("ths_index_prices_historical", {
        "thscode": INDEX_CODE, "interval": "1d",
        "start": int(datetime.combine(start, datetime.min.time(), CN_TZ).timestamp() * 1000),
        "end": int(session_close(end).timestamp() * 1000),
    })
    return [{"trade_date": datetime.fromtimestamp(item["date_ms"] / 1000, CN_TZ).date().isoformat(),
             "close": float(item["close_price"]), "volume": float(item["volume"]), "source": "fuyao_ths"}
            for item in (envelope.get("data") or {}).get("item") or []
            if item.get("date_ms") and item.get("close_price") and item.get("volume")]


async def tencent_index_bars(start: date, end: date) -> list[dict[str, Any]]:
    from .datasources.http import request_json
    payload = await request_json("GET", TENCENT_KLINE_URL, params={
        "param": f"sh000001,day,{start.isoformat()},{end.isoformat()},{(end - start).days + 10},",
    })
    return [{"trade_date": bar[0], "close": float(bar[2]), "volume": float(bar[5]), "source": "tencent_free"}
            for bar in ((payload.get("data") or {}).get("sh000001") or {}).get("day") or [] if float(bar[5]) > 0]


async def fetch_index(start: date, end: date, *, primary: Callable[[date, date], Awaitable[list[dict[str, Any]]]] = fuyao_index_bars,
                      fallback: Callable[[date, date], Awaitable[list[dict[str, Any]]]] = tencent_index_bars) -> dict[str, Any]:
    """The whole window from one source; ``errors`` names each source that failed."""
    errors = []
    for fetch in (primary, fallback):
        try:
            bars = await fetch(start, end)
        except Exception as error:  # noqa: BLE001 - try the next source
            errors.append(f"{getattr(fetch, '__name__', 'source')}: {type(error).__name__}")
            continue
        if bars:
            return {"bars": sorted(bars, key=lambda bar: bar["trade_date"]), "errors": errors}
        errors.append(f"{getattr(fetch, '__name__', 'source')}: no rows")
    return {"bars": [], "errors": errors}


def timing_states(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replay the state over the bars (oldest first); one reading per bar once MA25 and VOL60 exist."""
    closes = [bar["close"] for bar in bars]
    volumes = [bar["volume"] for bar in bars]
    golden, readings = False, []
    for index in range(max(LONG, VOLUME_LONG - 1), len(bars)):
        ma_short = fmean(closes[index - SHORT + 1:index + 1])
        ma_long = fmean(closes[index - LONG + 1:index + 1])
        ma_long_before = fmean(closes[index - LONG:index])
        volume_ratio = fmean(volumes[index - VOLUME_SHORT + 1:index + 1]) / fmean(volumes[index - VOLUME_LONG + 1:index + 1])
        was = golden
        if ma_short <= ma_long:
            golden = False
        elif not golden and volume_ratio > 1:
            golden = True
        readings.append({
            "trade_date": bars[index]["trade_date"], "state": "golden" if golden else "silver",
            "event": ("golden" if golden else "silver") if golden != was else None,
            "close": closes[index], "ma5": round(ma_short, 3), "ma25": round(ma_long, 3),
            "ma25_rising": ma_long > ma_long_before, "volume_ratio": round(volume_ratio, 4),
            "source": bars[index]["source"],
        })
    return readings


async def refresh(database: Any, trade_date: date, *, run_database: Callable[..., Awaitable[Any]],
                  fetch: Callable[[date, date], Awaitable[dict[str, Any]]] | None = None,
                  keep: int = KEEP_SESSIONS, apply: bool = True) -> dict[str, Any]:
    """Fetch, replay and store; ``completed`` only when the session itself has a state."""
    fetched = await (fetch or fetch_index)(trade_date - timedelta(days=FETCH_DAYS), trade_date)
    bars = [bar for bar in fetched["bars"] if bar["trade_date"] <= trade_date.isoformat()]
    readings = [{**reading, "version": VERSION, "research_only": True, "live_effect": "none"}
                for reading in timing_states(bars)][-keep:]
    counts = {"stored": 0, "unchanged": 0}
    if apply and readings:
        counts = await run_database(derived_daily_readings.store, database, CAPABILITY, readings, timeout_seconds=60)
    latest = readings[-1] if readings else None
    completed = bool(latest and latest["trade_date"] == trade_date.isoformat())
    return {
        "status": "completed" if completed else "blocked", "trade_date": trade_date.isoformat(), "bars": len(bars),
        "errors": fetched["errors"], **counts,
        "latest": {key: latest[key] for key in ("trade_date", "state", "event", "source")} if latest else None,
        "reason": None if completed else "no index bar for the session yet",
        "research_only": True, "live_effect": "none",
    }


__all__ = ["CAPABILITY", "INDEX_CODE", "VERSION", "fetch_index", "fuyao_index_bars", "refresh", "tencent_index_bars",
           "timing_states"]
