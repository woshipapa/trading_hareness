"""Research-only runtime orchestration for tactic-trial readings."""

from __future__ import annotations

import argparse
import asyncio
import json
import resource
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from statistics import median
from typing import Any, Mapping, Sequence

from . import derived_daily_readings, limit_up_risk_repository, tactic_trial_repository
from .derived_daily_readings import CN_TZ, session_close
from .limit_up_risk import decode_limit_rows
from .runtime_executors import ExecutorSaturatedError
from .tactic_conditions import (
    build_auction_row, build_day_context, build_trial_row, evaluate_conditions, history_features,
)
from .tactic_exits import evaluate_pick
from .t1_settlement import SettlementBar, opens_at_up_limit
from .auction_open_desk import gap_bucket
from .tactic_trials import (
    TACTICS, History, PairHistory, Tactic, daily_summary, day_board, day_digest, day_pairs, json_safe,
    registry_fingerprint, tactic_card, tactic_day,
)

VERSION = "tactic-trials-v1"
CLOSE_CAPABILITY = "tactic_trials_close_daily"
AUCTION_CAPABILITY = "tactic_trials_auction_daily"
# One document a session and capability is what the store keeps (`derived_daily_readings.newest`), so everything
# that exists per slot has a capability per slot.
SLOTS = ("close", "auction")
READING_CAPABILITY = {"close": CLOSE_CAPABILITY, "auction": AUCTION_CAPABILITY}
DIGEST_CAPABILITY = {"close": "tactic_trials_digest_close_daily", "auction": "tactic_trials_digest_auction_daily"}
PAIRS_CAPABILITY = {"close": "tactic_trials_pairs_close_daily", "auction": "tactic_trials_pairs_auction_daily"}
PAIR_SESSIONS = 250    # the mined pairs look at this many sessions back: their day documents are the large ones
WINDOW_SESSIONS = 12   # a pick day is settled again every evening until every sell rule has left it, at most this long
SUMMARY_CAPABILITY = "tactic_trials_summary_daily"
RISK_CAPABILITY = "limit_up_risk_daily"
REPLAY_DISAGREEMENT_LIMIT = 0.005
# Sessions a replay holds at once. The owner host has 894 MB: a chunk's bars, exit paths and settled documents
# are tens of megabytes at 20 sessions; the 250-session windows are read one day at a time.
REPLAY_CHUNK_SESSIONS = 20


def _research(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The payload marked as research evidence, in plain JSON types (the store's adapter takes no Decimal)."""
    return json_safe({**payload, "research_only": True, "live_effect": "none"})


def _today() -> date:
    return datetime.now(CN_TZ).date()


def _read(database: Any, function: Any, *args: Any) -> Any:
    with database.transaction() as connection:
        return function(connection, *args)


def _stored(connection: Any, capability: str, day: date) -> dict[str, Any] | None:
    """The day's stored document of the current version."""
    documents = tactic_trial_repository.read_capability(connection, capability, day, day)
    return documents[-1] if documents and documents[-1].get("version") == VERSION else None


def _newest(connection: Any, capability: str, last: date) -> dict[str, Any] | None:
    """The newest stored document up to ``last``, if it is of the current version."""
    document = tactic_trial_repository.read_newest(connection, capability, last)
    return document if document is not None and document.get("version") == VERSION else None


def _prior_sessions(connection: Any, day: date, count: int = 3) -> list[date]:
    sessions = tactic_trial_repository.read_open_sessions(connection, day - timedelta(days=30), day - timedelta(days=1))
    return sessions[-count:]


def _previous_readings(connection: Any, day: date) -> list[Mapping[str, Any] | None]:
    sessions = _prior_sessions(connection, day)
    if not sessions:
        return []
    readings = tactic_trial_repository.read_capability(connection, CLOSE_CAPABILITY, sessions[0], sessions[-1])
    by_day = {reading["trade_date"]: reading for reading in readings}
    return [by_day.get(session.isoformat()) for session in reversed(sessions)]


def _market_context(turnover: Sequence[Mapping[str, Any]], day: date) -> tuple[Any, Any]:
    current = next((row.get("market_turnover") for row in turnover if row.get("trading_date") == day), None)
    prior = [row.get("market_turnover") for row in turnover if row.get("trading_date") < day and row.get("market_turnover") is not None][-60:]
    return current, (median(prior) if prior else None)


def _build_close_reading(
    trade_date: date, lists: Mapping[str, Sequence[Sequence[Any]]], risk: Mapping[str, Any],
    bars: Mapping[str, Mapping[str, Any]], windows: Mapping[tuple[str, date], Sequence[Mapping[str, Any]]],
    turnover: Sequence[Mapping[str, Any]], previous: Sequence[Mapping[str, Any] | None],
    *, replayed: bool = False,
) -> dict[str, Any]:
    decoded = decode_limit_rows({key: list(value) for key, value in lists.items()}, dict(bars))
    risk_by_symbol = {row["symbol"]: row for row in risk.get("rows", [])}
    rows = []
    for decoded_row in decoded["rows"]:
        window = list(windows.get((decoded_row["symbol"], trade_date), ()))
        features = history_features(window)
        row = build_trial_row(decoded_row, features, risk_by_symbol.get(decoded_row["symbol"], {}), decoded["rows"])
        row["reference_close"] = decoded_row["bar"].get("close")
        row["history_basis"] = features.get("history_basis")
        rows.append(row)
    market_turnover, market_median = _market_context(turnover, trade_date)
    yesterday = previous[0] if previous and previous[0] and previous[0].get("complete") else None
    yesterday_rows = (yesterday or {}).get("rows", [])
    top_boards = max((float(row["boards"]) for row in yesterday_rows if row.get("boards") is not None), default=None)
    top_symbols = {row["symbol"] for row in yesterday_rows if top_boards is not None and float(row.get("boards")) == top_boards}
    prev_top = None if top_boards is None else any(row["symbol"] in top_symbols for row in rows)
    context = build_day_context(rows, market_turnover, market_median, prev_top)
    evaluated = evaluate_conditions(rows, context, "close", previous)
    return _research({
        "trade_date": trade_date.isoformat(), "slot": "close", "version": VERSION,
        "decided_at": session_close(trade_date).isoformat(), "day": evaluated["day"],
        "rows": evaluated["rows"], "coverage": decoded["coverage"], "complete": True, "replayed": replayed,
    })


def refresh_close(database: Any, trade_date: date, *, apply: bool = True) -> dict[str, Any]:
    with database.transaction() as connection:
        stored_reading = _stored(connection, CLOSE_CAPABILITY, trade_date)
        if stored_reading:
            return _research({"status": "already_stored", "trade_date": trade_date.isoformat(), "rows": len(stored_reading.get("rows", [])), "stored": 0})
        list_reading = tactic_trial_repository.read_limit_lists(connection, trade_date, trade_date)
        risk = tactic_trial_repository.read_capability(connection, RISK_CAPABILITY, trade_date, trade_date)
        day_lists = next((item for item in list_reading["days"] if item["trade_date"] == trade_date), None)
        risk_reading = risk[-1] if risk else None
        if day_lists is None or risk_reading is None:
            return _research({"status": "blocked", "trade_date": trade_date.isoformat(), "stored": 0})
        requested_symbols = limit_up_risk_repository.symbols(day_lists["lists"])
        bars = {str(row["symbol"]): row for row in tactic_trial_repository.read_day_bars(connection, trade_date, trade_date, requested_symbols)}
        history = tactic_trial_repository.read_history_windows(connection, [(symbol, trade_date) for symbol in requested_symbols])
        windows: dict[tuple[str, date], list[Mapping[str, Any]]] = {}
        for row in history:
            windows.setdefault((str(row["symbol"]), trade_date), []).append(row)
        turnover = tactic_trial_repository.read_market_turnover(connection, trade_date - timedelta(days=120), trade_date)
        previous = _previous_readings(connection, trade_date)
        reading = _build_close_reading(trade_date, day_lists["lists"], risk_reading, bars, windows, turnover, previous)
    stored = derived_daily_readings.store(database, CLOSE_CAPABILITY, [reading]) if apply else {"stored": 0, "unchanged": 0}
    return _research({"status": "completed", "trade_date": trade_date.isoformat(), "rows": len(reading["rows"]), **stored})


def _build_auction_reading(
    trade_date: date, desk_rows: Sequence[Mapping[str, Any]], previous_close: Mapping[str, Any] | None,
    decided_at: str, source: str,
) -> dict[str, Any]:
    previous_by_symbol = {row["symbol"]: row for row in (previous_close or {}).get("rows", [])}
    rows = []
    for desk_row in desk_rows:
        previous_row = previous_by_symbol.get(desk_row["symbol"])
        row = build_auction_row(desk_row, desk_rows, previous_row)
        row["reference_close"] = previous_row["reference_close"] if previous_row else None
        rows.append(row)
    evaluated = evaluate_conditions(rows, {"flags": [], "unknown": []}, "auction")
    evaluated["day"]["prev"] = (previous_close or {}).get("day")
    return _research({"trade_date": trade_date.isoformat(), "slot": "auction", "version": VERSION,
        "decided_at": decided_at, "day": evaluated["day"], "rows": evaluated["rows"],
        "complete": True, "source": source})


def refresh_auction(database: Any, trade_date: date, *, apply: bool = True) -> dict[str, Any]:
    """The auction reading of a session, from its stored 09:25 table; decided when that table was stored."""
    with database.transaction() as connection:
        if _stored(connection, AUCTION_CAPABILITY, trade_date):
            return _research({"status": "already_stored", "trade_date": trade_date.isoformat(), "stored": 0})
        table = tactic_trial_repository.read_auction_table(connection, trade_date)
        if table is None:
            return _research({"status": "not_ready", "trade_date": trade_date.isoformat(), "stored": 0})
        previous = _prior_sessions(connection, trade_date, 1)
        previous_reading = _stored(connection, CLOSE_CAPABILITY, previous[0]) if previous else None
        reading = _build_auction_reading(
            trade_date, table["rows"], previous_reading if previous_reading and previous_reading["complete"] else None,
            table["stored_at"], "auction_open_desk_daily",
        )
    stored = derived_daily_readings.store(database, AUCTION_CAPABILITY, [reading]) if apply else {"stored": 0, "unchanged": 0}
    return _research({"status": "completed", "trade_date": trade_date.isoformat(), "rows": len(reading["rows"]), **stored})


def _settled_complete(result: Mapping[str, Any]) -> bool:
    for entries in result.get("entries", {}).values():
        for item in entries.values():
            if item.get("status") not in {"settled", "no_trade"}:
                return False
    return True


def _decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _settle_reading(reading: Mapping[str, Any], bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    """Every row's outcomes under every entry and sell rule, from the bars given. Prices enter as Decimal."""
    pick_day = date.fromisoformat(reading["trade_date"])
    settled_rows = []
    complete = True
    for row in reading["rows"]:
        bars = bars_by_symbol.get(row["symbol"], ())
        prior_bars = [bar for bar in bars if bar["trading_date"] < pick_day and bar.get("close") is not None]
        path = [bar for bar in bars if bar["trading_date"] >= pick_day]
        adjusted = all(bar.get("adj_factor") is not None and bar["adj_factor"] > 0 for bar in (*prior_bars, *path))
        # A prior close is put on the pick day's price basis; evaluate_pick moves it to the path's basis.
        pick_factor = _decimal(path[0]["adj_factor"]) if adjusted and path else Decimal(1)
        prior_closes = [_decimal(bar["close"]) * _decimal(bar["adj_factor"]) / pick_factor if adjusted else _decimal(bar["close"])
                        for bar in prior_bars]
        outcome = evaluate_pick(row["symbol"], reading["slot"], path, _decimal(row.get("reference_close")), prior_closes)
        complete = complete and _settled_complete(outcome)
        settled_rows.append({"symbol": row["symbol"], "name": row.get("name"), "outcomes": outcome})
    return {"trade_date": reading["trade_date"], "slot": reading["slot"], "rows": settled_rows, "complete": complete}


def day_outcomes(database: Any, reading: Mapping[str, Any], as_of: date) -> dict[str, Any]:
    """A reading with every row's outcomes from the bars up to ``as_of``, and whether every sell rule has left it.

    Nothing of it is stored: the outcomes of a pick day are the reading plus the bars, and can be had again.
    """
    pick_day = date.fromisoformat(reading["trade_date"])
    bars_by_symbol: dict[str, list[Mapping[str, Any]]] = {}
    for bar in _read(database, tactic_trial_repository.read_exit_bars, [(row["symbol"], pick_day) for row in reading["rows"]]):
        if bar["trading_date"] <= as_of:
            bars_by_symbol.setdefault(str(bar["symbol"]), []).append(bar)
    settled = _settle_reading(reading, bars_by_symbol)
    return {**reading, "outcomes": {row["symbol"]: row["outcomes"] for row in settled["rows"]}, "settled": settled["complete"]}


def _digest_documents(day: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The day's digest and its pairs: what the evening summary folds instead of the rows."""
    return (_research({**day_digest(day), "version": VERSION}), _research({**day_pairs(day), "version": VERSION}))


def _store_final(database: Any, slot: str, digest: Mapping[str, Any], pairs: Mapping[str, Any]) -> None:
    """Close a pick day: its pairs first, its digest last. A stored digest is what says the day is final."""
    derived_daily_readings.store(database, PAIRS_CAPABILITY[slot], [dict(pairs)])
    derived_daily_readings.store(database, DIGEST_CAPABILITY[slot], [dict(digest)])


def _current(digest: Mapping[str, Any], registry: str) -> bool:
    return digest.get("version") == VERSION and digest.get("registry") == registry


def summarise(database: Any, as_of: date, *, apply: bool = True) -> dict[str, Any]:
    """Settle what is still open, fold every pick day's digest, and store the evening's summary.

    Older pick days are read as stored digests, chunk by chunk. The pick days of the last ``WINDOW_SESSIONS``
    sessions without a final digest are settled now, one day at a time, from the stored bars; a day is closed
    (its digest stored, once) when every sell rule has left it, or when it is about to leave the window. The day
    sections of the summary describe, for each slot, the newest pick day that has outcomes.
    """
    registry = registry_fingerprint()
    sessions = _read(database, tactic_trial_repository.read_open_sessions, as_of - timedelta(days=1200), as_of)
    window, older = sessions[-WINDOW_SESSIONS:], sessions[:-WINDOW_SESSIONS]
    pair_first = sessions[-PAIR_SESSIONS:][0]
    history, pairs, stale = History(), PairHistory(), 0
    for offset in range(0, len(older), tactic_trial_repository.CHUNK_SESSIONS):
        chunk = older[offset:offset + tactic_trial_repository.CHUNK_SESSIONS]
        with database.transaction() as connection:
            for slot in SLOTS:
                for digest in tactic_trial_repository.read_capability(connection, DIGEST_CAPABILITY[slot], chunk[0], chunk[-1]):
                    if _current(digest, registry):
                        history.add(digest)
                    else:
                        stale += 1
                if chunk[-1] >= pair_first:
                    for item in tactic_trial_repository.read_capability(connection, PAIRS_CAPABILITY[slot], max(chunk[0], pair_first), chunk[-1]):
                        pairs.add(item)
    folded: dict[tuple[str, str], tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    latest: dict[str, dict[str, Any]] = {}
    today_rows: dict[str, int] = {}
    open_days = 0
    for slot in SLOTS:
        with database.transaction() as connection:
            final = {item["trade_date"]: item for item in tactic_trial_repository.read_capability(
                connection, DIGEST_CAPABILITY[slot], window[0], window[-1]) if _current(item, registry)}
            final_pairs = {item["trade_date"]: item for item in tactic_trial_repository.read_capability(
                connection, PAIRS_CAPABILITY[slot], window[0], window[-1])}
            readings = [reading for reading in tactic_trial_repository.read_capability(
                connection, READING_CAPABILITY[slot], window[0], window[-1]) if reading.get("version") == VERSION]
        newest_resolved = None
        for reading in readings:
            day_key = reading["trade_date"]
            if day_key == as_of.isoformat():
                today_rows[slot] = len(reading["rows"])
            if day_key in final and day_key in final_pairs:
                digest, pair_document = final[day_key], final_pairs[day_key]
            else:
                day = day_outcomes(database, reading, as_of)
                digest, pair_document = _digest_documents(day)
                if day["settled"] or day_key == window[0].isoformat():
                    if apply:
                        _store_final(database, slot, digest, pair_document)
                else:
                    open_days += 1
                if digest["resolved"]:
                    latest[slot] = day
            folded[day_key, slot] = (digest, pair_document)
            if digest["resolved"]:
                newest_resolved = reading
        if newest_resolved is not None and latest.get(slot, {}).get("trade_date") != newest_resolved["trade_date"]:
            latest[slot] = day_outcomes(database, newest_resolved, as_of)
    for day_key, slot in sorted(folded):
        digest, pair_document = folded[day_key, slot]
        history.add(digest)
        if date.fromisoformat(day_key) >= pair_first:
            pairs.add(pair_document)
    previous = _read(database, _newest, SUMMARY_CAPABILITY, as_of - timedelta(days=1))
    summary = daily_summary(history, pairs, [latest[slot] for slot in SLOTS if slot in latest], previous, today_rows)
    summary = _research({**summary, "trade_date": as_of.isoformat(), "version": VERSION, "registry": registry,
                         "pair_sessions": PAIR_SESSIONS, "open_days": open_days, "stale_digest_days": stale})
    stored = derived_daily_readings.store(database, SUMMARY_CAPABILITY, [summary]) if apply else {"stored": 0, "unchanged": 0}
    return _research({"status": "completed", "trade_date": as_of.isoformat(), **stored,
                      "summary": {"records": len(summary["records"]), "lines": len(summary["lines"]), "open_days": open_days,
                                  "stale_digest_days": stale,
                                  "bytes": len(json.dumps(summary, ensure_ascii=False))}})


def evening(database: Any, trade_date: date) -> dict[str, Any]:
    """One session's evening run: the readings, then the summary (which settles what is still open).

    Started by the peer's evening loop after the day's limit-up risk reading exists (decision 0021). The receipt of
    that stage closes on ``completed`` only; ``blocked`` is tried again on the next tick. Two things block it:
    the day's lists or risk reading are not stored, or the store holds no close reading at all in the window (the
    history replay has not been applied yet: a first day built without its previous sessions would have every
    condition that looks back unknown, and the replay never replaces a reading built live).
    """
    with database.transaction() as connection:
        sessions = tactic_trial_repository.read_open_sessions(connection, trade_date - timedelta(days=30), trade_date)[-WINDOW_SESSIONS:]
        known = tactic_trial_repository.read_capability(connection, CLOSE_CAPABILITY, sessions[0], sessions[-1])
    if not known:
        return _research({"status": "blocked", "trade_date": trade_date.isoformat(),
                          "reason": "no close reading in the last sessions: apply the history replay first"})
    repaired = None
    if len(sessions) > 1 and known[-1]["trade_date"] < sessions[-2].isoformat():
        # The evening before was missed: its readings are built first, from the lists and the risk reading the
        # loop repairs for that session (decision 0021); older sessions are not repaired here.
        repaired = {"auction": refresh_auction(database, sessions[-2])["status"], "close": refresh_close(database, sessions[-2])["status"]}
    auction = refresh_auction(database, trade_date)
    close = refresh_close(database, trade_date)
    if close["status"] == "blocked":
        return _research({"status": "blocked", "trade_date": trade_date.isoformat(), "repaired": repaired,
                          "reason": "the day's limit lists or its risk reading are not stored"})
    summary = summarise(database, trade_date)
    return _research({"status": "completed", "trade_date": trade_date.isoformat(), "close": close["status"],
                      "auction": auction["status"], "repaired": repaired, "summary": summary["summary"]})


def read_summary(database: Any, day: date | None = None) -> dict[str, Any]:
    """The newest summary up to a day (today when none is given): before the evening run that is the one before."""
    reading = _read(database, _newest, SUMMARY_CAPABILITY, day or _today())
    return _research({"version": VERSION, "reading": reading})


def read_board(database: Any, day: date | None = None) -> dict[str, Any]:
    """A session's board: its auction picks by side, the close picks of the session before, each tactic's label.

    Without a day it is the session after the newest close reading: in the evening that is tomorrow's board (the
    close picks are there, the auction is not yet); when the calendar does not hold that session yet, today's.
    The labels are those of the newest summary before the board's day: what was known when the board was built.
    """
    with database.transaction() as connection:
        today = _today()
        if day is None:
            newest_close = _newest(connection, CLOSE_CAPABILITY, today)
            following = [] if newest_close is None else tactic_trial_repository.read_sessions_after(
                connection, date.fromisoformat(newest_close["trade_date"]), 1)
            day = following[0] if following else today
        auction = _stored(connection, AUCTION_CAPABILITY, day)
        previous = _prior_sessions(connection, day, 1)
        close = _stored(connection, CLOSE_CAPABILITY, previous[0]) if previous else None
        summary = _newest(connection, SUMMARY_CAPABILITY, day - timedelta(days=1))
    records = summary["records"] if summary else {}
    return _research({
        "version": VERSION, "trade_date": day.isoformat(),
        "auction": None if auction is None else {"decided_at": auction["decided_at"], "source": auction["source"],
                                                 "rows": len(auction["rows"]), **day_board(auction)},
        "close": None if close is None else {"trade_date": close["trade_date"], "rows": len(close["rows"]), **day_board(close)},
        "labels_as_of": summary["trade_date"] if summary else None,
        "tactics": [{**tactic_card(tactic), "label": records.get(tactic.key, {}).get("label"), "q": records.get(tactic.key, {}).get("q")}
                    for tactic in TACTICS if tactic.combination],
    })


def tactic_by_key(key: str) -> Tactic | None:
    return next((tactic for tactic in TACTICS if tactic.key == key), None)


def read_cases(database: Any, tactic: Tactic, days: int = 10) -> dict[str, Any]:
    """A tactic's pick days among the last ``days`` sessions, newest first, with outcomes as the bars give them now.

    One session at a time, and the bars are read only for a day the tactic picked something on.
    """
    today = _today()
    sessions = _read(database, tactic_trial_repository.read_open_sessions, today - timedelta(days=days * 2 + 14), today)[-days:]
    result = []
    for session in reversed(sessions):
        reading = _read(database, _stored, READING_CAPABILITY[tactic.slot], session)
        if reading is None or not tactic_day({**reading, "outcomes": {}}, tactic)["picks"]:
            continue
        item = tactic_day(day_outcomes(database, reading, today), tactic)
        # Settled for this view: every pick has the tactic's own outcome (the day's other sell rules may still be open).
        result.append({**item, "settled": all(pick["primary"] is not None for pick in item["picks"])})
    return _research({"version": VERSION, "tactic": tactic_card(tactic), "sessions": len(sessions), "days": result})


DATABASE_UNAVAILABLE = (ExecutorSaturatedError, TimeoutError)
AUCTION_FROM, AUCTION_UNTIL = time(9, 25, 5), time(9, 45)


@dataclass(frozen=True)
class TacticAuctionDependencies:
    database: Any
    run_database: Callable[..., Awaitable[Any]]
    session_open: Callable[[date], Awaitable[bool]]
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep


async def auction_reading(deps: TacticAuctionDependencies, trade_date: date) -> str:
    """Wait for the day's stored 09:25 table, then store the auction reading; the status says how the morning ended.

    The stored table is the only interface to the auction capture. A saturated executor or a timed-out call is
    waited out; any other database error ends the task, and the task supervisor starts it again.
    """
    session, status = None, "not_ready"
    while deps.now().astimezone(CN_TZ).time() <= AUCTION_UNTIL:
        try:
            if session is None:
                session = await deps.session_open(trade_date)
            if not session:
                return "not_a_session"
            status = (await deps.run_database(refresh_auction, deps.database, trade_date, timeout_seconds=10))["status"]
        except DATABASE_UNAVAILABLE:
            status = "database_busy"
        if status not in ("not_ready", "database_busy"):
            return status
        await deps.sleep(5)
    return "no_table" if status == "not_ready" else status


async def run_tactic_auction_loop(deps: TacticAuctionDependencies, *, log: Callable[[str], None] = print) -> None:
    """Every session morning from 09:25:05: the auction reading of the tactic ledger, built once.

    A morning this loop misses (the service was down) is made up by the evening run, from the same stored table.
    """
    while True:
        now = deps.now().astimezone(CN_TZ)
        start = datetime.combine(now.date(), AUCTION_FROM, tzinfo=CN_TZ)
        if now.weekday() < 5 and AUCTION_FROM <= now.time() <= AUCTION_UNTIL:
            log(f"tactic_auction trade_date={now.date().isoformat()} status={await auction_reading(deps, now.date())}")
            now = deps.now().astimezone(CN_TZ)
        if now >= start:
            start += timedelta(days=1)
        await deps.sleep((start - now).total_seconds())


def _report_view(report: Mapping[str, Mapping[str, int]]) -> dict[str, dict[str, Any]]:
    return {year: {"days_built": value["days"], "rows": value["rows"],
                   "skipped_days": value["skipped_days"],
                   "disagreement_share": value["not_at_limit"] / (value["rows"] + value["not_at_limit"])
                   if value["rows"] + value["not_at_limit"] else None,
                   "raw_basis_share": value["raw_returns"] / value["returns"] if value["returns"] else None,
                   "history_raw_basis_share": value["history_raw"] / value["rows"] if value["rows"] else None}
            for year, value in report.items()}


def _report_add(report: dict[str, dict[str, int]], reading: Mapping[str, Any], outcomes: Mapping[str, Mapping[str, Any]]) -> None:
    year = str(reading["trade_date"])[:4]
    value = report.setdefault(year, {"days": 0, "rows": 0, "skipped_days": 0,
                                     "not_at_limit": 0, "history_raw": 0, "returns": 0, "raw_returns": 0})
    value["days"] += 1
    value["rows"] += len(reading.get("rows", []))
    value["not_at_limit"] += reading.get("coverage", {}).get("not_at_limit", 0)
    value["history_raw"] += sum(row.get("history_basis") == "unadjusted_missing_adj_factor" for row in reading.get("rows", []))
    for row_outcomes in outcomes.values():
        for entries in row_outcomes["entries"].values():
            for outcome in entries.values():
                if outcome.get("status") == "settled":
                    value["returns"] += 1
                    value["raw_returns"] += outcome.get("price_basis") == "unadjusted_missing_adj_factor"


def replay(database: Any, slot: str, start: date, end: date, *, apply: bool = False) -> dict[str, Any]:
    if slot not in {"close", "auction"}:
        raise ValueError("slot must be close or auction")
    sessions = _read(database, tactic_trial_repository.read_open_sessions, start, end)
    chunks = [sessions[index:index + REPLAY_CHUNK_SESSIONS] for index in range(0, len(sessions), REPLAY_CHUNK_SESSIONS)]
    report: dict[str, dict[str, int]] = {}
    recent: list[tuple[date, Mapping[str, Any] | None]] = []
    skipped: dict[str, int] = {}
    if sessions:
        with database.transaction() as connection:
            prior_sessions = tactic_trial_repository.read_open_sessions(connection, sessions[0] - timedelta(days=30), sessions[0] - timedelta(days=1))[-3:]
            prior_readings = tactic_trial_repository.read_capability(connection, CLOSE_CAPABILITY, prior_sessions[0], prior_sessions[-1]) if prior_sessions else []
        by_day = {reading["trade_date"]: reading for reading in prior_readings}
        recent = [(day, by_day.get(day.isoformat())) for day in prior_sessions]
    all_sessions = ([day for day, _reading in recent] + sessions)
    today = _today()
    # A pick day older than the evening run's window is closed as it stands; a newer one only when it is settled,
    # and otherwise left to the evening run.
    window_first = _read(database, tactic_trial_repository.read_open_sessions, today - timedelta(days=30), today)[-WINDOW_SESSIONS:][0]
    refused = None
    for chunk_index, chunk in enumerate(chunks):
        first, last = chunk[0], chunk[-1]
        documents: list[Mapping[str, Any]] = []
        # A reading built live is evidence of what was known then: the replay uses it and never replaces it.
        live = {reading["trade_date"]: reading
                for reading in _read(database, tactic_trial_repository.read_capability, READING_CAPABILITY[slot], first, last)
                if reading.get("version") == VERSION and not reading.get("replayed")}
        if slot == "close":
            lists = _read(database, tactic_trial_repository.read_limit_lists, first, last)
            risk_rows = _read(database, tactic_trial_repository.read_capability, RISK_CAPABILITY, first, last)
            risk = {str(item["trade_date"]): item for item in risk_rows}
            lists_by_day = {item["trade_date"]: item for item in lists["days"]}
            symbols_by_day = {day: limit_up_risk_repository.symbols(item["lists"])
                              for day, item in lists_by_day.items()}
            symbols = sorted({symbol for day_symbols in symbols_by_day.values() for symbol in day_symbols})
            if symbols:
                with database.transaction() as connection:
                    bar_rows = tactic_trial_repository.read_day_bars(connection, first, last, symbols)
                    turnover = tactic_trial_repository.read_market_turnover(connection, first - timedelta(days=120), last)
            else:
                bar_rows, turnover = [], []
            bars_by_day: dict[date, dict[str, Mapping[str, Any]]] = {}
            for row in bar_rows:
                bars_by_day.setdefault(row["trading_date"], {})[str(row["symbol"])] = row
            for day in chunk:
                previous = [reading for _date, reading in reversed(recent)]
                item = lists_by_day.get(day)
                if item is None:
                    recent.append((day, None))
                    recent = recent[-3:]
                    continue
                reading = live.get(day.isoformat())
                if reading is None:
                    windows: dict[tuple[str, date], list[Mapping[str, Any]]] = {}
                    for row in _read(database, tactic_trial_repository.read_history_windows,
                                     [(symbol, day) for symbol in symbols_by_day[day]]):
                        windows.setdefault((str(row["symbol"]), row["pick_day"]), []).append(row)
                    reading = _build_close_reading(
                        day, item["lists"], risk.get(day.isoformat(), {}), bars_by_day.get(day, {}), windows,
                        turnover, previous, replayed=True,
                    )
                recent.append((day, reading))
                recent = recent[-3:]
                documents.append(reading)
        else:
            auction_rows = _read(database, tactic_trial_repository.read_auction_lists, first, last)
            by_day = {str(item["trade_date"]): item for item in auction_rows}
            position = all_sessions.index(first)
            previous_session = all_sessions[position - 1] if position else None
            close_readings = _read(database, tactic_trial_repository.read_capability, CLOSE_CAPABILITY,
                                    previous_session or first, last)
            close_by_day = {reading["trade_date"]: reading for reading in close_readings}
            symbols = sorted({row["symbol"] for reading in close_readings for row in reading.get("rows", [])})
            with database.transaction() as connection:
                bar_rows = tactic_trial_repository.read_day_bars(connection, first, last, symbols)
            bars_by_day = {}
            for row in bar_rows:
                bars_by_day.setdefault(str(row["trading_date"]), {})[str(row["symbol"])] = row
            for day in chunk:
                if day.isoformat() in live:
                    documents.append(live[day.isoformat()])
                    continue
                auction = by_day.get(day.isoformat(), {"rows": []})
                prior_session = all_sessions[all_sessions.index(day) - 1] if all_sessions.index(day) else None
                previous_close = close_by_day.get(prior_session.isoformat()) if prior_session else None
                year = day.isoformat()[:4]
                if not previous_close or not previous_close.get("complete"):
                    skipped[year] = skipped.get(year, 0) + 1
                    continue
                auction_by_symbol = {item["symbol"]: item for item in auction.get("rows", [])}
                desk_rows = []
                for previous_row in previous_close.get("rows", []):
                    symbol = previous_row["symbol"]
                    item = auction_by_symbol.get(symbol, {})
                    bar = bars_by_day.get(day.isoformat(), {}).get(symbol)
                    if bar is None:
                        continue
                    open_price, pre_close = bar.get("open"), bar.get("pre_close")
                    gap = None if open_price is None or pre_close in (None, 0) else open_price / pre_close - 1
                    desk_rows.append({"symbol": symbol, "name": previous_row.get("name"), "open": open_price, "gap": gap,
                        "opens_at_limit": opens_at_up_limit(symbol, SettlementBar.from_row(bar)),
                        "bucket": gap_bucket(gap, opened_at_limit=opens_at_up_limit(symbol, SettlementBar.from_row(bar))) if gap is not None else None,
                        "boards": previous_row.get("boards"), "amount": item.get("amount"), "volume": bar.get("volume"),
                        "sealed_from_open": previous_row.get("sealed_from_open")})
                if not desk_rows:
                    continue
                reading = _build_auction_reading(
                    day, desk_rows, previous_close, datetime.combine(day, time(9, 25), CN_TZ).isoformat(),
                    "bars+auction_list",
                )
                reading["replayed"] = True
                documents.append(reading)
        finals = []
        for reading in documents:
            day = day_outcomes(database, reading, today)
            _report_add(report, reading, day["outcomes"])
            if day["settled"] or date.fromisoformat(reading["trade_date"]) < window_first:
                finals.append(_digest_documents(day))
        for year, count in skipped.items():
            report.setdefault(year, {"days": 0, "rows": 0, "skipped_days": 0, "not_at_limit": 0, "history_raw": 0, "returns": 0, "raw_returns": 0})["skipped_days"] = count
        refused = next((year for year, value in _report_view(report).items()
                        if value["disagreement_share"] is not None and value["disagreement_share"] > REPLAY_DISAGREEMENT_LIMIT), None)
        if apply and refused is not None:
            return _research({"status": "refused", "reason": "limit_membership_disagreement", "year": refused,
                              "stored_chunks": chunk_index, "years": _report_view(report)})
        if apply:
            derived_daily_readings.store(database, READING_CAPABILITY[slot], [reading for reading in documents if reading["trade_date"] not in live])
            for digest, pair_document in finals:
                _store_final(database, slot, digest, pair_document)
    return _research({"status": "completed", "slot": slot, "start": start.isoformat(), "end": end.isoformat(),
                      "years": _report_view(report), "days": sum(value["days"] for value in report.values()),
                      "rows": sum(value["rows"] for value in report.values()), "replayed": True})


def rebuild_digests(database: Any, slot: str, start: date, end: date, *, apply: bool = False) -> dict[str, Any]:
    """Compute the digests of the stored readings of a range again, from the bars, and store those that are final.

    For after a change to the tactic registry: the readings stay as they are.
    """
    today = _today()
    window_first = _read(database, tactic_trial_repository.read_open_sessions, today - timedelta(days=30), today)[-WINDOW_SESSIONS:][0]
    days = stored = 0
    for session in _read(database, tactic_trial_repository.read_open_sessions, start, end):
        reading = _read(database, _stored, READING_CAPABILITY[slot], session)
        if reading is None:
            continue
        day = day_outcomes(database, reading, today)
        days += 1
        if apply and (day["settled"] or session < window_first):
            _store_final(database, slot, *_digest_documents(day))
            stored += 1
    return _research({"status": "completed", "slot": slot, "start": start.isoformat(), "end": end.isoformat(),
                      "days": days, "stored": stored, "registry": registry_fingerprint()})


def main() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot", choices=SLOTS)
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--summarise", type=date.fromisoformat)
    parser.add_argument("--digests", action="store_true", help="rebuild the digests of the stored readings instead of replaying")
    args = parser.parse_args()
    from .database import Database
    database = Database(); database.open()
    if args.summarise:
        result = summarise(database, args.summarise, apply=args.apply)
    else:
        result = (rebuild_digests if args.digests else replay)(database, args.slot, args.start, args.end, apply=args.apply)
    # ru_maxrss is in kilobytes on Linux, where this runs: the run's peak memory beside its result.
    print(json.dumps({**result, "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024}, ensure_ascii=False))


if __name__ == "__main__":
    main()


__all__ = [
    "TacticAuctionDependencies", "auction_reading", "day_outcomes", "evening", "read_board", "read_cases", "read_summary",
    "rebuild_digests", "refresh_auction", "refresh_close", "replay", "run_tactic_auction_loop", "summarise", "tactic_by_key",
]
