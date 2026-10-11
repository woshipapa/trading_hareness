"""Short-term sentiment temperature (情绪温度), daily and intraday (decision 0013).

A-share short-term traders read the day's mood from a handful of board statistics:
- how many stocks close limit-up and limit-down;
- how many limit-ups break (炸板);
- how high the consecutive-limit ladder climbs, and how many of yesterday's
  limit-ups advance another board (晋级率);
- how yesterday's limit-ups trade today (the money effect, 昨日涨停溢价);
- breadth, and turnover against its own recent average (量能比). The level of
  turnover tracks the market regime more than the day's mood: through
  September 2026 it ranked below a year of sessions every day and pulled every
  reading down.

None of them alone is a thermometer. Each is turned into a 0-100 score by its
percentile rank among the preceding ``WINDOW`` sessions (the construction of
CNN's Fear & Greed Index), inverted where more means colder (limit-downs). The
temperature is the mean of the available scores: 80 and above is a boiling
point (沸点), 20 and below a freezing point (冰点).

Scores are point in time: a day is ranked against itself and the sessions
before it, never against later ones. A component whose inputs are missing is
left out rather than read as zero. On 2026-08-27 and 2026-09-29 the daily bars
carry no limit prices; counted as zero, those days became false freezing
points and broke every limit-up streak.

Research only: the temperature describes the day; it does not decide anything.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

VERSION = "market-temperature-v1"


@dataclass(frozen=True)
class Component:
    key: str
    label: str
    direction: int          # +1: more is warmer; -1: more is colder


COMPONENTS: tuple[Component, ...] = (
    Component("limit_up", "涨停数", 1),
    Component("limit_down", "跌停数", -1),
    Component("seal_rate", "封板率", 1),
    Component("up_ratio", "上涨比", 1),
    Component("premium", "昨日涨停溢价", 1),
    Component("promotion", "连板晋级率", 1),
    Component("max_streak", "最高连板", 1),
    Component("turnover_ratio", "量能比", 1),
)
KEYS = tuple(component.key for component in COMPONENTS)
WINDOW = 250                # sessions a value is ranked against (about one year)
MIN_HISTORY = 60            # fewer sessions than this and a component is not scored
MIN_COMPONENTS = 5          # fewer scored components than this and there is no temperature
TURNOVER_BASE = 20          # sessions whose mean turnover the day's turnover is compared with
FREEZING, BOILING = 20.0, 80.0


def band_of(temperature: float | None) -> str | None:
    """冰点 ≤20 < 冷 <40 ≤ 中性 <60 ≤ 热 <80 ≤ 沸点."""
    if temperature is None:
        return None
    if temperature <= FREEZING:
        return "冰点"
    if temperature < 40:
        return "冷"
    if temperature < 60:
        return "中性"
    if temperature < BOILING:
        return "热"
    return "沸点"


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def components_of(row: Mapping[str, Any]) -> dict[str, float | None]:
    """The day's raw component values from one aggregate row of ``market_temperature_repository``.

    ``limits_ok`` says whether the day's bars carry limit prices; without them the
    limit-based components are unknown, not zero.
    """
    limits = bool(row.get("limits_ok"))
    touched = int(row.get("touched") or 0)
    advancers, decliners = int(row.get("advancers") or 0), int(row.get("decliners") or 0)
    previous = int(row.get("prev_sealed") or 0)
    limit_up = int(row.get("limit_up") or 0)
    return {
        "limit_up": float(limit_up) if limits else None,
        "limit_down": float(row.get("limit_down") or 0) if limits else None,
        "seal_rate": limit_up / touched if limits and touched else None,
        "up_ratio": advancers / (advancers + decliners) if advancers + decliners else None,
        "premium": _number(row.get("premium_pct")) if previous else None,
        "promotion": int(row.get("promoted") or 0) / previous if limits and previous else None,
        "max_streak": float(row.get("max_streak") or 0) if limits else None,
        "turnover_ratio": _number(row.get("turnover_ratio")),
    }


def with_turnover_ratio(rows: Iterable[Mapping[str, Any]], *, base: int = TURNOVER_BASE) -> list[dict[str, Any]]:
    """Rows in date order, each given ``turnover_ratio``: its turnover over the mean of the ``base`` sessions before it."""
    out, previous = [], []
    for row in rows:
        item = dict(row)
        turnover = _number(item.get("turnover_cny"))
        recent = previous[-base:]
        item["turnover_ratio"] = (turnover / (sum(recent) / len(recent))
                                  if turnover is not None and len(recent) >= base and sum(recent) > 0 else None)
        if turnover is not None and turnover > 0:
            previous.append(turnover)
        out.append(item)
    return out


def percentile_rank(value: float, history: Sequence[float]) -> float:
    """The share of ``history`` below ``value``, ties counted half, as 0-100."""
    ordered = sorted(history)
    below = bisect.bisect_left(ordered, value)
    ties = bisect.bisect_right(ordered, value) - below
    return 100.0 * (below + 0.5 * ties) / len(ordered)


def score(values: Mapping[str, float | None], history: Mapping[str, Sequence[float]], *,
          min_history: int = MIN_HISTORY) -> tuple[dict[str, float | None], float | None]:
    """Each component's 0-100 score against ``history``, and their mean as the temperature."""
    scores: dict[str, float | None] = {}
    for component in COMPONENTS:
        value, past = values.get(component.key), history.get(component.key) or ()
        if value is None or len(past) < min_history:
            scores[component.key] = None
            continue
        rank = percentile_rank(value, past)
        scores[component.key] = round(rank if component.direction > 0 else 100.0 - rank, 1)
    available = [item for item in scores.values() if item is not None]
    temperature = round(sum(available) / len(available), 1) if len(available) >= MIN_COMPONENTS else None
    return scores, temperature


def temperature_series(rows: Iterable[Mapping[str, Any]], *, window: int = WINDOW,
                       min_history: int = MIN_HISTORY) -> list[dict[str, Any]]:
    """Aggregate rows in date order -> one reading a day, each ranked against itself and the days before it."""
    history: dict[str, list[float]] = {key: [] for key in KEYS}
    readings = []
    for row in with_turnover_ratio(rows):
        values = components_of(row)
        for key, value in values.items():
            if value is not None:
                history[key].append(value)
        scores, temperature = score(values, {key: past[-window:] for key, past in history.items()},
                                    min_history=min_history)
        readings.append({
            "trade_date": str(row["trading_date"]), "temperature": temperature, "band": band_of(temperature),
            "scores": scores, "values": values, "index_close": _number(row.get("index_close")),
            "turnover_cny": _number(row.get("turnover_cny")), "limits_ok": bool(row.get("limits_ok")),
        })
    return readings


def history_for(rows: Iterable[Mapping[str, Any]], *, window: int = WINDOW) -> dict[str, list[float]]:
    """The last ``window`` daily values of each component: what an intraday reading is ranked against."""
    history: dict[str, list[float]] = {key: [] for key in KEYS}
    for row in with_turnover_ratio(rows):
        for key, value in components_of(row).items():
            if value is not None:
                history[key].append(value)
    return {key: past[-window:] for key, past in history.items()}


__all__ = [
    "BOILING", "COMPONENTS", "FREEZING", "KEYS", "MIN_COMPONENTS", "MIN_HISTORY", "TURNOVER_BASE", "VERSION", "WINDOW",
    "Component", "band_of", "components_of", "history_for", "percentile_rank", "score", "temperature_series",
    "with_turnover_ratio",
]
