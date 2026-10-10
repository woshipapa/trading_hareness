"""Intraday sentiment temperature: the daily temperature's components every five minutes (decision 0013).

Each sample reads the all-A minute cross-section (decision 0009) at its time and
computes the eight daily components as if the session closed there:
- limit-up and limit-down: stocks at the session's own limit prices now
  (``daily_trade_limits``, stored before the first intraday scan);
- the seal rate: stocks at limit-up now over those that touched it so far
  (the running high);
- breadth;
- yesterday's limit-ups: their mean move (the premium) and the share sealed
  again (promotion);
- the highest board: yesterday's streak + 1, for a stock sealed now;
- turnover: so far, scaled to a full-day equivalent by the share of the day's
  turnover usually done by that time, over the mean of the 20 sessions before.
  The share is the median over the last 20 stored sessions. Until 5 are
  stored, turnover is left out rather than guessed.

Each value is ranked against the same component's daily closing values over
the 250 sessions before, the daily temperature's own history. Intraday and
daily therefore share one scale, and the 15:00 sample is the day's reading
less one day of ranking history.

That history is read from the stored daily readings, in milliseconds. The
console relay gives a read 30 s, and the bars SQL takes about 13 s on a busy
owner. Only when fewer than 250 readings are stored (before the backfill) are
the bars aggregated again.

Samples run 09:30-11:30 and 13:05-15:00, every five minutes; the lunch break
is a gap, not a line. A finished session is stored after the close as
``market_temperature_intraday``, one reading holding its samples. The session
in progress is computed on request, and each settled sample is kept in this
process.

    python -m app.market_temperature_intraday --start 2026-09-01 --end 2026-10-09 [--apply]

backfills past sessions oldest first, so later ones can use the turnover
profile of the earlier ones. A session takes about 15 s: its 49 minute
documents and the 420-day daily history.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta
from statistics import fmean, median
from typing import Any

from . import derived_daily_readings, minute_cross_section
from .derived_daily_readings import CN_TZ
from .market_temperature import KEYS, TURNOVER_BASE, VERSION, WINDOW, band_of, components_of, history_for, score
from .market_temperature_repository import (
    LIMIT_TOLERANCE, LIMITS_OK_SHARE, daily_rows, prior_streaks, session_limits, sessions_between,
)
from .market_temperature_runtime import CAPABILITY as DAILY_CAPABILITY

CAPABILITY = "market_temperature_intraday"
STEP = timedelta(minutes=5)
SESSIONS = ((time(9, 30), time(11, 30)), (time(13, 5), time(15, 0)))
CLOSE = time(15, 0)
SAMPLE_WINDOW = timedelta(minutes=3)       # a sample is the first minute captured within this after its time
LOOKBACK_DAYS = 600                        # daily history, as in market_temperature_runtime: a full 250-session window
PROFILE_SESSIONS, PROFILE_MIN = 20, 5
MIN_SAMPLES = 40                           # of 49, for a stored session to count as complete
COUNT_KEYS = ("stocks", "with_limits", "limit_up", "touched", "limit_down", "advancers", "decliners",
              "prev_sealed", "promoted", "max_streak")


def _sample_times() -> tuple[time, ...]:
    times = []
    for start, end in SESSIONS:
        moment = datetime.combine(date(2000, 1, 3), start)
        while moment.time() <= end:
            times.append(moment.time())
            moment += STEP
    return tuple(times)


SAMPLE_TIMES = _sample_times()


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def aggregate(rows: Iterable[Mapping[str, Any]], limits: Mapping[str, tuple[float, float | None]],
              prior: Mapping[str, int]) -> dict[str, Any]:
    """The daily aggregate's fields from one minute's rows; a row without a trade or a previous close is left out."""
    counts = dict.fromkeys(COUNT_KEYS, 0)
    turnover, premium = 0.0, []
    for row in rows:
        raw = row.get("raw") or {}
        symbol = str(row.get("symbol") or row.get("ts_code") or "").upper()
        price, previous = _number(row.get("price")), _number(raw.get("prev_price"))
        if not symbol or not price or not previous or price <= 0 or previous <= 0 or not _number(row.get("volume")):
            continue
        counts["stocks"] += 1
        turnover += _number(row.get("turnover")) or 0.0
        up, down = limits.get(symbol, (None, None))
        sealed = bool(up) and price >= up - LIMIT_TOLERANCE
        if up:
            counts["with_limits"] += 1
            counts["touched"] += max(_number(raw.get("high_price")) or price, price) >= up - LIMIT_TOLERANCE
        counts["limit_up"] += sealed
        counts["limit_down"] += bool(down) and price <= down + LIMIT_TOLERANCE
        move = price / previous - 1
        counts["advancers"] += move > 0
        counts["decliners"] += move < 0
        if symbol in prior:
            counts["prev_sealed"] += 1
            counts["promoted"] += sealed
            premium.append(move)
        if sealed:
            counts["max_streak"] = max(counts["max_streak"], prior.get(symbol, 0) + 1)
    return {**counts, "turnover_cny": turnover, "premium_pct": fmean(premium) * 100 if premium else None,
            "limits_ok": counts["with_limits"] >= LIMITS_OK_SHARE * counts["stocks"] > 0}


def share_profile(connection: Any, day: date) -> dict[str, float]:
    """Per sample time, the median share of the day's turnover done by then, over the last stored sessions."""
    stored = derived_daily_readings.newest(connection, CAPABILITY, day - timedelta(days=60), day - timedelta(days=1))
    shares: dict[str, list[float]] = {}
    for reading in stored[-PROFILE_SESSIONS:]:
        for sample in reading.get("samples") or []:
            if sample.get("share"):
                shares.setdefault(sample["time"], []).append(float(sample["share"]))
    return {moment: round(median(values), 5) for moment, values in shares.items() if len(values) >= PROFILE_MIN}


_CONTEXTS: dict[date, dict[str, Any]] = {}
_SAMPLES: dict[tuple[date, str], dict[str, Any]] = {}


def stored_history(connection: Any, day: date) -> tuple[dict[str, list[float]], float | None] | None:
    """The ranking history and turnover base from the stored daily readings, or None while too few are stored."""
    stored = derived_daily_readings.newest(connection, DAILY_CAPABILITY, day - timedelta(days=LOOKBACK_DAYS),
                                           day - timedelta(days=1))
    recent = [reading.get("turnover_cny") for reading in stored[-TURNOVER_BASE:]]
    if len(stored) < WINDOW or None in recent:
        return None
    history = {key: [float(reading["values"][key]) for reading in stored
                     if (reading.get("values") or {}).get(key) is not None][-WINDOW:] for key in KEYS}
    return history, fmean(float(value) for value in recent)


def bars_history(connection: Any, day: date) -> tuple[dict[str, list[float]], float | None]:
    """The same from the bars: one pass of the daily SQL, about 13 s on the owner."""
    rows = daily_rows(connection, day - timedelta(days=LOOKBACK_DAYS), day - timedelta(days=1))
    turnovers = [float(row["turnover_cny"]) for row in rows if row.get("turnover_cny")][-TURNOVER_BASE:]
    return history_for(rows), fmean(turnovers) if len(turnovers) == TURNOVER_BASE else None


def daily_context(connection: Any, day: date) -> dict[str, Any]:
    """Everything a sample of ``day`` is ranked or matched against; kept once the session's limits are stored."""
    if day in _CONTEXTS:
        return _CONTEXTS[day]
    history, turnover_base = stored_history(connection, day) or bars_history(connection, day)
    previous, prior = prior_streaks(connection, day)
    context = {
        "history": history, "turnover_base": turnover_base,
        "previous_session": previous, "prior": prior, "limits": session_limits(connection, day),
        "profile": share_profile(connection, day),
    }
    if context["limits"]:
        for stale in [key for key in _CONTEXTS if key < day - timedelta(days=3)]:
            _CONTEXTS.pop(stale)
        _CONTEXTS[day] = context
    return context


def _document_at(connection: Any, day: date, moment: time) -> tuple[datetime, dict[str, Any]] | None:
    if moment == CLOSE:
        found = minute_cross_section.latest(connection, day)        # the closing auction prints after 15:00
        if found and found[0] >= datetime.combine(day, CLOSE, CN_TZ):
            return found
    start = datetime.combine(day, moment, CN_TZ)
    return minute_cross_section.first_between(connection, start, start + SAMPLE_WINDOW)


def sample(connection: Any, day: date, moment: time, context: Mapping[str, Any]) -> dict[str, Any] | None:
    found = _document_at(connection, day, moment)
    if found is None:
        return None
    observed_at, document = found
    totals = aggregate(minute_cross_section.rows_of(document), context["limits"], context["prior"])
    share, base = context["profile"].get(moment.strftime("%H:%M")), context["turnover_base"]
    ratio = totals["turnover_cny"] / share / base if share and base and totals["turnover_cny"] else None
    values = components_of({**totals, "turnover_ratio": ratio})
    scores, temperature = score(values, context["history"])
    return {
        "time": moment.strftime("%H:%M"), "observed_at": observed_at.isoformat(), "temperature": temperature,
        "band": band_of(temperature), "scores": scores, "values": values,
        "counts": {key: totals[key] for key in COUNT_KEYS}, "turnover_cny": round(totals["turnover_cny"]),
        "limits_ok": totals["limits_ok"],
    }


def intraday_series(connection: Any, day: date, *, now: datetime | None = None) -> dict[str, Any]:
    """The samples of ``day`` up to ``now``; a missing minute leaves a gap."""
    now = now or datetime.now(CN_TZ)
    context = daily_context(connection, day)
    samples = []
    for moment in SAMPLE_TIMES:
        at = datetime.combine(day, moment, CN_TZ)
        if at > now:
            break
        key = (day, moment.strftime("%H:%M"))
        reading = _SAMPLES.get(key) or sample(connection, day, moment, context)
        if reading is None:
            continue
        if key not in _SAMPLES and context["limits"] and now >= at + SAMPLE_WINDOW and moment != CLOSE:
            _SAMPLES[key] = reading
        samples.append(reading)
    for stale in [key for key in _SAMPLES if key[0] < day - timedelta(days=3)]:
        _SAMPLES.pop(stale)
    return {
        "trade_date": day.isoformat(), "version": VERSION, "samples": samples,
        "previous_session": context["previous_session"].isoformat() if context["previous_session"] else None,
        "limits": len(context["limits"]), "profile_sessions": PROFILE_SESSIONS if context["profile"] else 0,
        "research_only": True, "live_effect": "none",
    }


def refresh(database: Any, day: date, *, apply: bool = True) -> dict[str, Any]:
    """After the close: compute every sample of ``day`` and store them, each with its share of the day's turnover."""
    with database.transaction() as connection:
        series = intraday_series(connection, day, now=datetime.combine(day, time(23, 59), CN_TZ))
    samples = [dict(item) for item in series["samples"]]
    close = next((item for item in samples if item["time"] == CLOSE.strftime("%H:%M")), None)
    for item in samples:
        item["share"] = round(item["turnover_cny"] / close["turnover_cny"], 5) if close and close["turnover_cny"] else None
    complete = len(samples) >= MIN_SAMPLES and close is not None and close["temperature"] is not None
    reading = {key: value for key, value in series.items() if key != "samples"} | {"samples": samples}
    counts = derived_daily_readings.store(database, CAPABILITY, [reading]) if apply and samples else {"stored": 0, "unchanged": 0}
    return {
        "status": "completed" if complete else "blocked", "trade_date": day.isoformat(), "samples": len(samples), **counts,
        "close_temperature": close["temperature"] if close else None,
        "reason": None if complete else "too few minute samples, or no limit prices stored for the session",
        "research_only": True, "live_effect": "none",
    }


def read(connection: Any, day: date | None = None) -> dict[str, Any]:
    """A finished session from storage; the session in progress (or one not stored yet) computed now."""
    day = day or datetime.now(CN_TZ).date()
    stored = derived_daily_readings.newest(connection, CAPABILITY, day, day)
    if stored:
        return {**stored[-1], "source": "stored"}
    return {**intraday_series(connection, day), "source": "live"}


def backfill(database: Any, start: date, end: date, *, apply: bool = True) -> list[dict[str, Any]]:
    """``refresh`` for every session in ``[start, end]``, oldest first."""
    with database.transaction() as connection:
        days = sessions_between(connection, start, end)
    return [refresh(database, day, apply=apply) for day in days]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="intraday sentiment temperature backfill (decision 0013)")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    from .database import Database
    database = Database()
    database.open()
    results = backfill(database, args.start, args.end, apply=args.apply)
    for result in results:
        print(json.dumps({key: result[key] for key in ("trade_date", "status", "samples", "stored", "close_temperature")},
                         ensure_ascii=False, default=str))
    return 0 if results and all(result["status"] == "completed" for result in results) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["CAPABILITY", "SAMPLE_TIMES", "aggregate", "backfill", "daily_context", "intraday_series", "main", "read",
           "refresh", "sample", "share_profile"]
