"""Research-only runtime orchestration for tactic-trial readings."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timedelta
from statistics import median
from typing import Any, Mapping, Sequence

from . import derived_daily_readings, limit_up_risk_repository, tactic_trial_repository
from .derived_daily_readings import CN_TZ, session_close
from .limit_up_risk import decode_limit_rows
from .tactic_conditions import (
    build_auction_row, build_day_context, build_trial_row, evaluate_conditions, history_features,
)
from .tactic_exits import evaluate_pick
from .t1_settlement import SettlementBar, opens_at_up_limit
from .auction_open_desk import gap_bucket
from .tactic_trials import build_daily_summary

VERSION = "tactic-trials-v1"
CLOSE_CAPABILITY = "tactic_trials_close_daily"
AUCTION_CAPABILITY = "tactic_trials_auction_daily"
SETTLED_CAPABILITY = "tactic_trials_settled_daily"
SUMMARY_CAPABILITY = "tactic_trials_summary_daily"
RISK_CAPABILITY = "limit_up_risk_daily"
DESK_CAPABILITY = "auction_open_desk_daily"
REPLAY_DISAGREEMENT_LIMIT = 0.005


def _research(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {**payload, "research_only": True, "live_effect": "none"}


def _read(database: Any, function: Any, *args: Any) -> Any:
    with database.transaction() as connection:
        return function(connection, *args)


def _reading_version(readings: Sequence[Mapping[str, Any]], day: date) -> Mapping[str, Any] | None:
    for reading in reversed(readings):
        if reading.get("trade_date") == day.isoformat() and reading.get("version") == VERSION:
            return reading
    return None


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
        existing = tactic_trial_repository.read_capability(connection, CLOSE_CAPABILITY, trade_date, trade_date)
        stored_reading = _reading_version(existing, trade_date)
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
        row["reference_close"] = previous_row.get("close") if previous_row else None
        rows.append(row)
    evaluated = evaluate_conditions(rows, {"flags": [], "unknown": []}, "auction")
    evaluated["day"]["prev"] = (previous_close or {}).get("day")
    return _research({"trade_date": trade_date.isoformat(), "slot": "auction", "version": VERSION,
        "decided_at": decided_at, "day": evaluated["day"], "rows": evaluated["rows"],
        "complete": True, "source": source})


def refresh_auction(database: Any, trade_date: date, *, apply: bool = True) -> dict[str, Any]:
    with database.transaction() as connection:
        existing = tactic_trial_repository.read_capability(connection, AUCTION_CAPABILITY, trade_date, trade_date)
        if _reading_version(existing, trade_date):
            return _research({"status": "already_stored", "trade_date": trade_date.isoformat(), "stored": 0})
        desk = tactic_trial_repository.read_capability(connection, DESK_CAPABILITY, trade_date, trade_date)
        if not desk:
            return _research({"status": "not_ready", "trade_date": trade_date.isoformat(), "stored": 0})
        desk_rows = desk[-1].get("rows", [])
        previous = _prior_sessions(connection, trade_date, 1)
        close_readings = tactic_trial_repository.read_capability(connection, CLOSE_CAPABILITY, previous[0], previous[-1]) if previous else []
        previous_reading = close_readings[-1] if close_readings else None
        previous_reading = previous_reading if previous_reading and previous_reading.get("complete") else None
        reading = _build_auction_reading(
            trade_date, desk_rows, previous_reading, desk[-1]["scheduled_at"], "auction_open_desk_daily",
        )
    stored = derived_daily_readings.store(database, AUCTION_CAPABILITY, [reading]) if apply else {"stored": 0, "unchanged": 0}
    return _research({"status": "completed", "trade_date": trade_date.isoformat(), "rows": len(reading["rows"]), **stored})


def _settled_complete(result: Mapping[str, Any]) -> bool:
    for entries in result.get("entries", {}).values():
        for item in entries.values():
            if item.get("status") not in {"settled", "no_trade"}:
                return False
    return True


def _settle_reading(reading: Mapping[str, Any], bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    pick_day = date.fromisoformat(reading["trade_date"])
    settled_rows = []
    complete = True
    for row in reading.get("rows", []):
        bars = bars_by_symbol.get(row["symbol"], ())
        prior_bars = [bar for bar in bars if bar["trading_date"] < pick_day and bar.get("close") is not None]
        path = [bar for bar in bars if bar["trading_date"] >= pick_day]
        adjusted = all(bar.get("adj_factor") is not None and float(bar["adj_factor"]) > 0 for bar in (*prior_bars, *path))
        pick_factor = float(path[0]["adj_factor"]) if adjusted and path else 1.0
        prior_closes = [float(bar["close"]) * float(bar["adj_factor"]) / pick_factor if adjusted else bar["close"] for bar in prior_bars]
        outcome = evaluate_pick(row["symbol"], reading["slot"], path, row.get("reference_close"), prior_closes)
        complete = complete and _settled_complete(outcome)
        settled_rows.append({"symbol": row["symbol"], "name": row.get("name"), "outcomes": outcome})
    return _research({"trade_date": reading["trade_date"], "slot": reading["slot"], "version": VERSION,
        "rows": settled_rows, "complete": complete})


def settle(database: Any, as_of: date, *, apply: bool = True) -> dict[str, Any]:
    with database.transaction() as connection:
        sessions = tactic_trial_repository.read_open_sessions(connection, as_of - timedelta(days=30), as_of)[-12:]
        close_readings = tactic_trial_repository.read_capability(connection, CLOSE_CAPABILITY, sessions[0] if sessions else as_of, as_of)
        auction_readings = tactic_trial_repository.read_capability(connection, AUCTION_CAPABILITY, sessions[0] if sessions else as_of, as_of)
        settled = tactic_trial_repository.read_capability(connection, SETTLED_CAPABILITY, sessions[0] if sessions else as_of, as_of)
        settled_by_key = {(item.get("trade_date"), item.get("slot")): item for item in settled}
        pending = [reading for reading in close_readings + auction_readings
                   if not settled_by_key.get((reading.get("trade_date"), reading.get("slot")), {}).get("complete")]
        symbol_days = [(row["symbol"], date.fromisoformat(reading["trade_date"])) for reading in pending for row in reading.get("rows", [])]
        bars = tactic_trial_repository.read_exit_bars(connection, symbol_days)
    bars_by_key: dict[tuple[str, date], list[Mapping[str, Any]]] = {}
    for bar in bars:
        bars_by_key.setdefault((str(bar["symbol"]), bar["pick_day"]), []).append(bar)
    written = []
    documents = []
    for reading in pending:
        pick_day = date.fromisoformat(reading["trade_date"])
        document = _settle_reading(reading, {row["symbol"]: bars_by_key.get((row["symbol"], pick_day), []) for row in reading.get("rows", [])})
        documents.append(document)
        if apply:
            written.append(derived_daily_readings.store(database, SETTLED_CAPABILITY, [document]))
    return _research({"status": "completed", "as_of": as_of.isoformat(), "days": [{"trade_date": item["trade_date"], "complete": item["complete"]} for item in documents], "stored": written})


def _settled_days(close: Sequence[Mapping[str, Any]], auction: Sequence[Mapping[str, Any]], settled: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    source = {(item["trade_date"], item["slot"]): item for item in close + auction}
    result = []
    for item in settled:
        base = source.get((item["trade_date"], item["slot"]))
        if base is None:
            continue
        outcomes = {row["symbol"]: row["outcomes"] for row in item.get("rows", [])}
        result.append({**base, "outcomes": outcomes})
    return sorted(result, key=lambda item: (item["trade_date"], item["slot"]))


def summarise(database: Any, as_of: date, *, apply: bool = True) -> dict[str, Any]:
    with database.transaction() as connection:
        first = as_of - timedelta(days=1200)
        close = tactic_trial_repository.read_capability(connection, CLOSE_CAPABILITY, first, as_of)
        auction = tactic_trial_repository.read_capability(connection, AUCTION_CAPABILITY, first, as_of)
        settled = tactic_trial_repository.read_capability(connection, SETTLED_CAPABILITY, first, as_of)
        days = _settled_days(close, auction, settled)
        previous_rows = derived_daily_readings.newest(connection, SUMMARY_CAPABILITY, first, as_of - timedelta(days=1))
        previous_summary = previous_rows[-1] if previous_rows else None
    summary = build_daily_summary(days, previous_summary)
    summary = _research({**summary, "trade_date": as_of.isoformat(), "version": VERSION})
    stored = derived_daily_readings.store(database, SUMMARY_CAPABILITY, [summary]) if apply else {"stored": 0, "unchanged": 0}
    return _research({"status": "completed", "trade_date": as_of.isoformat(), **stored,
                      "summary": {"records": len(summary["records"]), "lines": len(summary["lines"]),
                                  "bytes": len(json.dumps(summary, ensure_ascii=False, default=str))}})


def _report_view(report: Mapping[str, Mapping[str, int]]) -> dict[str, dict[str, Any]]:
    return {year: {"days_built": value["days"], "rows": value["rows"],
                   "skipped_days": value["skipped_days"],
                   "disagreement_share": value["not_at_limit"] / (value["rows"] + value["not_at_limit"])
                   if value["rows"] + value["not_at_limit"] else None,
                   "raw_basis_share": value["raw_returns"] / value["returns"] if value["returns"] else None,
                   "history_raw_basis_share": value["history_raw"] / value["rows"] if value["rows"] else None}
            for year, value in report.items()}


def _report_add(report: dict[str, dict[str, int]], reading: Mapping[str, Any], settled: Mapping[str, Any] | None) -> None:
    year = str(reading["trade_date"])[:4]
    value = report.setdefault(year, {"days": 0, "rows": 0, "skipped_days": 0,
                                     "not_at_limit": 0, "history_raw": 0, "returns": 0, "raw_returns": 0})
    value["days"] += 1
    value["rows"] += len(reading.get("rows", []))
    value["not_at_limit"] += reading.get("coverage", {}).get("not_at_limit", 0)
    value["history_raw"] += sum(row.get("history_basis") == "unadjusted_missing_adj_factor" for row in reading.get("rows", []))
    if settled:
        for row in settled.get("rows", []):
            for entries in row.get("outcomes", {}).get("entries", {}).values():
                for outcome in entries.values():
                    if outcome.get("status") == "settled":
                        value["returns"] += 1
                        value["raw_returns"] += outcome.get("price_basis") == "unadjusted_missing_adj_factor"


def replay(database: Any, slot: str, start: date, end: date, *, apply: bool = False) -> dict[str, Any]:
    if slot not in {"close", "auction"}:
        raise ValueError("slot must be close or auction")
    sessions = _read(database, tactic_trial_repository.read_open_sessions, start, end)
    chunks = [sessions[index:index + tactic_trial_repository.CHUNK_SESSIONS] for index in range(0, len(sessions), tactic_trial_repository.CHUNK_SESSIONS)]
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
    refused = None
    for chunk_index, chunk in enumerate(chunks):
        first, last = chunk[0], chunk[-1]
        documents: list[Mapping[str, Any]] = []
        settled_documents: list[Mapping[str, Any]] = []
        if slot == "close":
            lists = _read(database, tactic_trial_repository.read_limit_lists, first, last)
            risk_rows = _read(database, tactic_trial_repository.read_capability, RISK_CAPABILITY, first, last)
            risk = {str(item["trade_date"]): item for item in risk_rows}
            lists_by_day = {item["trade_date"]: item for item in lists["days"]}
            symbols_by_day = {day: limit_up_risk_repository.symbols(item["lists"])
                              for day, item in lists_by_day.items()}
            symbol_days = [(symbol, day) for day, day_symbols in symbols_by_day.items() for symbol in day_symbols]
            symbols = sorted({symbol for symbol, _day in symbol_days})
            if symbol_days:
                with database.transaction() as connection:
                    bar_rows = tactic_trial_repository.read_day_bars(connection, first, last, symbols)
                    window_rows = tactic_trial_repository.read_history_windows(connection, symbol_days)
                    turnover = tactic_trial_repository.read_market_turnover(connection, first - timedelta(days=120), last)
            else:
                bar_rows, window_rows, turnover = [], [], []
            windows: dict[tuple[str, date], list[Mapping[str, Any]]] = {}
            for row in window_rows:
                windows.setdefault((str(row["symbol"]), row["pick_day"]), []).append(row)
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
        symbol_days = [(row["symbol"], date.fromisoformat(reading["trade_date"])) for reading in documents for row in reading.get("rows", [])]
        exit_bars = _read(database, tactic_trial_repository.read_exit_bars, symbol_days)
        bars_by_key: dict[tuple[str, date], list[Mapping[str, Any]]] = {}
        for bar in exit_bars:
            bars_by_key.setdefault((str(bar["symbol"]), bar["pick_day"]), []).append(bar)
        for reading in documents:
            pick_day = date.fromisoformat(reading["trade_date"])
            settled = {**_settle_reading(reading, {row["symbol"]: bars_by_key.get((row["symbol"], pick_day), []) for row in reading.get("rows", [])}), "replayed": True}
            settled_documents.append(settled)
            _report_add(report, reading, settled)
        for year, count in skipped.items():
            report.setdefault(year, {"days": 0, "rows": 0, "skipped_days": 0, "not_at_limit": 0, "history_raw": 0, "returns": 0, "raw_returns": 0})["skipped_days"] = count
        refused = next((year for year, value in _report_view(report).items()
                        if value["disagreement_share"] is not None and value["disagreement_share"] > REPLAY_DISAGREEMENT_LIMIT), None)
        if apply and refused is not None:
            return _research({"status": "refused", "reason": "limit_membership_disagreement", "year": refused,
                              "stored_chunks": chunk_index, "years": _report_view(report)})
        if apply and documents:
            capability = CLOSE_CAPABILITY if slot == "close" else AUCTION_CAPABILITY
            derived_daily_readings.store(database, capability, documents)
            derived_daily_readings.store(database, SETTLED_CAPABILITY, settled_documents)
    return _research({"status": "completed", "slot": slot, "start": start.isoformat(), "end": end.isoformat(),
                      "years": _report_view(report), "days": sum(value["days"] for value in report.values()),
                      "rows": sum(value["rows"] for value in report.values()), "replayed": True})


def main() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot", choices=("close", "auction"))
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--summarise", type=date.fromisoformat)
    args = parser.parse_args()
    from .database import Database
    database = Database(); database.open()
    result = summarise(database, args.summarise, apply=args.apply) if args.summarise else replay(database, args.slot, args.start, args.end, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()


__all__ = ["refresh_auction", "refresh_close", "replay", "settle", "summarise"]
