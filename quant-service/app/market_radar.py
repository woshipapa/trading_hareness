"""Market radar: where the whole market's turnover sits, minute by minute.

Reverse-engineered from a vendor chart ("情绪·系统雷达 · 主动买/卖 · 主力净额")
whose legend balances exactly - on 2026-10-08 at 11:09 it read 主动卖 4411.6亿,
主动买 3549.9亿, 中间带 2341.7亿 and 全池总额 10303.3亿 (4411.6 + 3549.9 +
2341.7 = 10303.2):

* 主动买 / 主动卖 - the day's turnover of every stock that has stood at or beyond
  +2% / -2% at some snapshot so far.  Membership only grows ("只进不出"), and a
  stock counts on the side it touched first, so the three bands partition the
  pool.  "即时" is the turnover of the stocks beyond the band right now.
* 中间带 - the rest of the pool (the 轧差).
* 全池总额 - the pool's turnover.
* 主力净额 - the market's main net inflow.  It is not in a price snapshot; the
  read model adds it from the board flow capture (the vendor sums Eastmoney's
  f62).

Finer than the original: a point every minute (the all-A capture's cadence)
instead of every two; the same bands at +/-5% and at the session's published
limit prices; every band split by board (Shanghai and Shenzhen main boards,
ChiNext, STAR, Beijing); counts as well as turnover; and the opening auction
kept as its own phase.  During 09:15-09:20 orders may still be withdrawn and
during 09:20-09:25 they may not; before 09:25 there is no turnover, only the
virtual price, so the auction phases report the price distribution and enter
no stock into the day's bands.  From the 09:25 match on, prices are real.

Pure: no I/O.  The runtime feeds it each persisted cross-section and stores
the returned point; ``RadarState`` is rebuilt from the stored points' entry
lists after a restart.  Research evidence, never an order.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

RADAR_VERSION = "market-radar-v1"
CN_TZ = ZoneInfo("Asia/Shanghai")
#: Percent bands, as the vendor's 2% and a stricter 5%.
THRESHOLDS = (2.0, 5.0)
SEGMENTS = ("sh_main", "sz_main", "chinext", "star", "beijing")
#: A price within this of a published limit is at the limit (prices tick at 0.01).
LIMIT_TOLERANCE = 0.005


def segment_of(symbol: str) -> str | None:
    code, _, exchange = str(symbol).upper().partition(".")
    if exchange == "BJ" or code.startswith(("920", "43", "83", "87")):
        return "beijing"
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("300", "301", "302")):
        return "chinext"
    if exchange == "SH" and code.startswith(("600", "601", "603", "605")):
        return "sh_main"
    if exchange == "SZ" and code.startswith(("000", "001", "002", "003")):
        return "sz_main"
    return None


def session_phase(observed_at: datetime) -> str:
    local = observed_at.astimezone(CN_TZ).time()
    if time(9, 15) <= local < time(9, 20):
        return "auction_cancellable"
    if time(9, 20) <= local < time(9, 25):
        return "auction_locked"
    if time(9, 25) <= local < time(9, 30):
        return "auction_matched"
    if time(9, 30) <= local <= time(11, 30) or time(13, 0) <= local < time(14, 57):
        return "continuous"
    if time(14, 57) <= local <= time(15, 0, 59):
        return "closing_auction"
    return "outside"


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


@dataclass
class RadarState:
    """The day's band membership: symbol -> the side it touched first."""

    trading_date: date
    first_touch: dict[str, dict[str, str]] = field(default_factory=dict)

    def side(self, band: str, symbol: str) -> str | None:
        return self.first_touch.get(band, {}).get(symbol)

    def enter(self, band: str, symbol: str, side: str) -> bool:
        members = self.first_touch.setdefault(band, {})
        if symbol in members:
            return False
        members[symbol] = side
        return True

    def restore(self, point: Mapping[str, Any]) -> None:
        """Replay one stored point's entries, oldest point first."""
        for band, sides in (point.get("entered") or {}).items():
            for side in ("up", "down"):
                for symbol in sides.get(side) or ():
                    self.enter(band, symbol, side)


def _cell() -> dict[str, float]:
    return {"turnover": 0.0, "count": 0}


def _add(cell: dict[str, float], turnover: float) -> None:
    cell["turnover"] += turnover
    cell["count"] += 1


def _bands_skeleton(band_keys: Iterable[str]) -> dict[str, dict[str, dict[str, float]]]:
    return {band: {name: _cell() for name in ("cum_up", "cum_down", "now_up", "now_down")} for band in band_keys}


def _finish(pool: dict[str, float], bands: dict[str, dict[str, dict[str, float]]]) -> None:
    """The middle band is the pool minus both sides, as a cumulative and as a now reading."""
    for cells in bands.values():
        cells["middle"] = {"turnover": pool["turnover"] - cells["cum_up"]["turnover"] - cells["cum_down"]["turnover"],
                           "count": pool["count"] - cells["cum_up"]["count"] - cells["cum_down"]["count"]}
        cells["now_middle"] = {"turnover": pool["turnover"] - cells["now_up"]["turnover"] - cells["now_down"]["turnover"],
                               "count": pool["count"] - cells["now_up"]["count"] - cells["now_down"]["count"]}


def radar_point(
    rows: Iterable[Mapping[str, Any]], state: RadarState, *, observed_at: datetime,
    limits: Mapping[str, tuple[float | None, float | None]] | None = None,
) -> dict[str, Any]:
    """One point from one complete cross-section; ``state`` gains the stocks first beyond a band now.

    ``rows`` carry ``ts_code`` (or ``symbol``), ``pct_change`` and the day's
    cumulative ``turnover`` in CNY.  ``limits`` maps a symbol to the session's
    published (up, down) limit prices; without it the limit band is omitted.
    """
    phase = session_phase(observed_at)
    counting = phase not in {"auction_cancellable", "auction_locked", "outside"}
    band_keys = [f"{threshold:g}" for threshold in THRESHOLDS] + (["limit"] if limits else [])
    pool = _cell()
    bands = _bands_skeleton(band_keys)
    segments = {segment: {"pool": _cell(), "bands": _bands_skeleton(band_keys)} for segment in SEGMENTS}
    entered: dict[str, dict[str, list[str]]] = {band: {"up": [], "down": []} for band in band_keys}
    auction = {"up": {f"{t:g}": 0 for t in THRESHOLDS}, "down": {f"{t:g}": 0 for t in THRESHOLDS}, "priced": 0}
    skipped = 0
    for row in rows:
        symbol = str(row.get("ts_code") or row.get("symbol") or "").upper()
        segment = segment_of(symbol)
        pct = _number(row.get("pct_change"))
        turnover = _number(row.get("turnover"))
        if segment is None or pct is None:
            skipped += 1
            continue
        if not counting:
            # Before the 09:25 match only the virtual price exists.
            auction["priced"] += 1
            for threshold in THRESHOLDS:
                if pct >= threshold:
                    auction["up"][f"{threshold:g}"] += 1
                elif pct <= -threshold:
                    auction["down"][f"{threshold:g}"] += 1
            continue
        if turnover is None or turnover < 0:
            skipped += 1
            continue
        _add(pool, turnover)
        _add(segments[segment]["pool"], turnover)
        sides: dict[str, str | None] = {}
        for threshold in THRESHOLDS:
            sides[f"{threshold:g}"] = "up" if pct >= threshold else "down" if pct <= -threshold else None
        if limits:
            up_limit, down_limit = (limits.get(symbol) or (None, None))
            price = _number(row.get("price"))
            sides["limit"] = (
                "up" if price is not None and up_limit and price >= up_limit - LIMIT_TOLERANCE
                else "down" if price is not None and down_limit and price <= down_limit + LIMIT_TOLERANCE
                else None
            )
        for band in band_keys:
            side = sides.get(band)
            if side is not None:
                _add(bands[band][f"now_{side}"], turnover)
                _add(segments[segment]["bands"][band][f"now_{side}"], turnover)
                if state.enter(band, symbol, side):
                    entered[band][side].append(symbol)
            first = state.side(band, symbol)
            if first is not None:
                _add(bands[band][f"cum_{first}"], turnover)
                _add(segments[segment]["bands"][band][f"cum_{first}"], turnover)
    _finish(pool, bands)
    for segment in segments.values():
        _finish(segment["pool"], segment["bands"])
    point: dict[str, Any] = {
        "radar_version": RADAR_VERSION, "trade_date": state.trading_date.isoformat(),
        "observed_at": observed_at.isoformat(), "phase": phase, "skipped_rows": skipped,
        "research_only": True, "live_effect": "none",
    }
    if counting:
        point.update(pool=pool, bands=bands, segments=segments,
                     entered={band: sides for band, sides in entered.items() if sides["up"] or sides["down"]})
    else:
        point["auction"] = auction
    return point


__all__ = [
    "CN_TZ", "LIMIT_TOLERANCE", "RADAR_VERSION", "RadarState", "SEGMENTS", "THRESHOLDS",
    "radar_point", "segment_of", "session_phase",
]
