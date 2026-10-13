"""Research-only runtime orchestration for tactic-trial readings."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any, Mapping, Sequence

from . import derived_daily_readings, tactic_trial_repository
from .derived_daily_readings import CN_TZ, session_close
from .limit_up_risk import decode_limit_rows
from .tactic_conditions import (
    build_auction_row, build_day_context, build_trial_row, evaluate_conditions, history_features,
)
from .tactic_exits import evaluate_pick
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


def _reading_version(readings: Sequence[Mapping[str, Any]], day: date, capability: str) -> Mapping[str, Any] | None:
    for reading in reversed(readings):
        if reading.get("trade_date") == day.isoformat() and reading.get("version") == VERSION:
            return reading
    return None


def _prior_sessions(connection: Any, day: date, count: int = 3) -> list[date]:
    sessions = tactic_trial_repository.read_open_sessions(connection, day - timedelta(days=30), day - timedelta(days=1))
    return sessions[-count:]


def _market_context(turnover: Sequence[Mapping[str, Any]], day: date) -> tuple[Any, Any]:
    values = [row.get("market_turnover") for row in turnover if row.get("market_turnover") is not None]
    current = next((row.get("market_turnover") for row in turnover if row.get("trading_date") == day), None)
    prior = [row.get("market_turnover") for row in turnover if row.get("trading_date") < day and row.get("market_turnover") is not None][-60:]
    return current, (median(prior) if prior else None)


def _close_rows(
    trade_date: date, lists: Mapping[str, Sequence[Sequence[Any]]], bars: Mapping[str, Mapping[str, Any]],
    risk: Mapping[str, Any], windows: Mapping[tuple[str, date], Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    decoded = decode_limit_rows({key: list(value) for key, value in lists.items()}, dict(bars))
    risk_by_symbol = {row["symbol"]: row for row in risk.get("rows", [])}
    rows = []
    for decoded_row in decoded["rows"]:
        window = list(windows.get((decoded_row["symbol"], trade_date), ()))
        features = history_features(window) if window else {"vr": None, "turnover_vs_max250": None, "runup_20": None,
            "close_vs_high60": None, "cost_share_below_close": None, "cost_share_0_30_above": None, "history_basis": "unadjusted_missing_adj_factor"}
        row = build_trial_row(decoded_row, features, risk_by_symbol.get(decoded_row["symbol"], {}), decoded["rows"])
        row["reference_close"] = decoded_row["bar"].get("close")
        row["history_basis"] = features.get("history_basis")
        row["bar_seal_disagreement"] = 0
        rows.append(row)
    return rows


def _close_reading(
    connection: Any, trade_date: date, lists: Mapping[str, Sequence[Sequence[Any]]], risk: Mapping[str, Any],
    *, replayed: bool = False, preloaded_bars: Mapping[str, Mapping[str, Any]] | None = None,
    preloaded_windows: Mapping[tuple[str, date], Sequence[Mapping[str, Any]]] | None = None,
    preloaded_turnover: Sequence[Mapping[str, Any]] | None = None,
    preloaded_previous: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    symbols = []
    for values in lists.values():
        for row in values:
            if isinstance(row, list) and row:
                symbols.append(str(row[0]))
    bars = dict(preloaded_bars) if preloaded_bars is not None else {
        str(row["symbol"]): row for row in tactic_trial_repository.read_day_bars(connection, trade_date, trade_date, symbols)
    }
    windows_rows = []
    if preloaded_windows is None:
        windows_rows = tactic_trial_repository.read_history_windows(
            connection, trade_date - timedelta(days=400), trade_date, [(symbol, trade_date) for symbol in symbols],
        )
    windows: dict[tuple[str, date], list[Mapping[str, Any]]] = {}
    if preloaded_windows is not None:
        windows = {key: list(value) for key, value in preloaded_windows.items() if key[1] == trade_date}
    else:
        for row in windows_rows:
            windows.setdefault((str(row["symbol"]), trade_date), []).append(dict(row))
    rows = _close_rows(trade_date, lists, bars, risk, windows)
    turnover = list(preloaded_turnover) if preloaded_turnover is not None else tactic_trial_repository.read_market_turnover(connection, trade_date - timedelta(days=120), trade_date)
    market_turnover, market_median = _market_context(turnover, trade_date)
    if preloaded_previous is not None:
        previous_readings = list(preloaded_previous)
    else:
        previous = _prior_sessions(connection, trade_date)
        previous_readings = tactic_trial_repository.read_capability(connection, CLOSE_CAPABILITY, previous[0] if previous else trade_date, previous[-1] if previous else trade_date)
    yesterday = previous_readings[-1] if previous_readings else None
    yesterday_rows = (yesterday or {}).get("rows", [])
    top = max(yesterday_rows, key=lambda row: float(row.get("boards", 0)), default=None)
    prev_top = None if top is None else any(row["symbol"] == top["symbol"] for row in rows)
    context = build_day_context(rows, market_turnover, market_median, prev_top)
    evaluated = evaluate_conditions(rows, context, "close", previous_readings[-3:])
    return _research({
        "trade_date": trade_date.isoformat(), "slot": "close", "version": VERSION,
        "decided_at": session_close(trade_date).isoformat(), "day": evaluated["day"],
        "rows": evaluated["rows"], "complete": True, "replayed": replayed,
    })


def refresh_close(database: Any, trade_date: date, *, apply: bool = True) -> dict[str, Any]:
    with database.transaction() as connection:
        existing = tactic_trial_repository.read_capability(connection, CLOSE_CAPABILITY, trade_date, trade_date)
        if _reading_version(existing, trade_date, CLOSE_CAPABILITY):
            return _research({"status": "already_stored", "trade_date": trade_date.isoformat(), "rows": len(existing[-1].get("rows", [])), "stored": 0, "reading": existing[-1]})
        list_reading = tactic_trial_repository.read_limit_lists(connection, trade_date, trade_date)
        risk = tactic_trial_repository.read_capability(connection, RISK_CAPABILITY, trade_date, trade_date)
        day_lists = next((item for item in list_reading["days"] if item["trade_date"] == trade_date), None)
        risk_reading = risk[-1] if risk else None
        if day_lists is None or risk_reading is None:
            return _research({"status": "blocked", "trade_date": trade_date.isoformat(), "stored": 0})
        reading = _close_reading(connection, trade_date, day_lists["lists"], risk_reading)
    stored = derived_daily_readings.store(database, CLOSE_CAPABILITY, [reading]) if apply else {"stored": 0, "unchanged": 0}
    return _research({"status": "completed", "trade_date": trade_date.isoformat(), "rows": len(reading["rows"]), **stored})


def refresh_auction(database: Any, trade_date: date, *, apply: bool = True) -> dict[str, Any]:
    with database.transaction() as connection:
        existing = tactic_trial_repository.read_capability(connection, AUCTION_CAPABILITY, trade_date, trade_date)
        if _reading_version(existing, trade_date, AUCTION_CAPABILITY):
            return _research({"status": "already_stored", "trade_date": trade_date.isoformat(), "stored": 0})
        desk = tactic_trial_repository.read_capability(connection, DESK_CAPABILITY, trade_date, trade_date)
        if not desk:
            return _research({"status": "not_ready", "trade_date": trade_date.isoformat(), "stored": 0})
        desk_rows = desk[-1].get("rows", [])
        previous = _prior_sessions(connection, trade_date, 1)
        close_readings = tactic_trial_repository.read_capability(connection, CLOSE_CAPABILITY, previous[0], previous[-1]) if previous else []
        previous_reading = close_readings[-1] if close_readings else None
        previous_by_symbol = {row["symbol"]: row for row in (previous_reading or {}).get("rows", [])} if previous_reading and previous_reading.get("complete") else {}
        rows = []
        for desk_row in desk_rows:
            row = build_auction_row(desk_row, desk_rows, previous_by_symbol.get(desk_row["symbol"]))
            row["reference_close"] = (previous_by_symbol.get(desk_row["symbol"]) or {}).get("close")
            rows.append(row)
        evaluated = evaluate_conditions(rows, {"flags": [], "unknown": []}, "auction")
        reading = _research({"trade_date": trade_date.isoformat(), "slot": "auction", "version": VERSION,
            "decided_at": datetime.now(CN_TZ).isoformat(), "day": evaluated["day"], "rows": evaluated["rows"],
            "complete": True, "source": "auction_open_desk_daily"})
    stored = derived_daily_readings.store(database, AUCTION_CAPABILITY, [reading]) if apply else {"stored": 0, "unchanged": 0}
    return _research({"status": "completed", "trade_date": trade_date.isoformat(), "rows": len(rows), **stored})


def _settled_complete(result: Mapping[str, Any]) -> bool:
    for entries in result.get("entries", {}).values():
        for item in entries.values():
            if item.get("status") not in {"settled", "no_trade", "missing_bar"}:
                return False
    return True


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
    by_symbol_day: dict[tuple[str, date], list[Mapping[str, Any]]] = {}
    for bar in bars:
        by_symbol_day.setdefault((bar["symbol"], bar["pick_day"]), []).append(bar)
    written = []
    for reading in pending:
        pick_day = date.fromisoformat(reading["trade_date"])
        slot = reading["slot"]
        settled_rows = []
        complete = True
        for row in reading.get("rows", []):
            path = by_symbol_day.get((row["symbol"], pick_day), [])
            outcome = evaluate_pick(row["symbol"], slot, path, row.get("reference_close"), row.get("prior_closes", ()))
            complete = complete and _settled_complete(outcome)
            settled_rows.append({"symbol": row["symbol"], "name": row.get("name"), "outcomes": outcome})
        document = _research({"trade_date": reading["trade_date"], "slot": slot, "version": VERSION,
            "rows": settled_rows, "complete": complete})
        if apply:
            written.append(derived_daily_readings.store(database, SETTLED_CAPABILITY, [document]))
    return _research({"status": "completed", "as_of": as_of.isoformat(), "days": [{"trade_date": item["trade_date"], "complete": item["complete"]} for item in pending], "stored": written})


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
        previous_rows = derived_daily_readings.newest(connection, SUMMARY_CAPABILITY, first, as_of)
        previous_summary = previous_rows[-1] if previous_rows else None
    summary = build_daily_summary(days, previous_summary)
    summary = _research({**summary, "trade_date": as_of.isoformat(), "version": VERSION})
    stored = derived_daily_readings.store(database, SUMMARY_CAPABILITY, [summary]) if apply else {"stored": 0, "unchanged": 0}
    return _research({"status": "completed", "trade_date": as_of.isoformat(), **stored, "summary": summary})


def _year_report(readings: Sequence[Mapping[str, Any]], limit_symbols: Mapping[str, set[str]]) -> dict[str, Any]:
    report: dict[str, dict[str, int]] = {}
    for reading in readings:
        year = str(reading["trade_date"])[:4]
        item = report.setdefault(year, {"days": 0, "rows": 0, "disagreements": 0, "raw": 0})
        item["days"] += 1
        for row in reading.get("rows", []):
            item["rows"] += 1
            item["disagreements"] += row.get("bar_seal_disagreement", 0)
            item["raw"] += row.get("history_basis") == "unadjusted_missing_adj_factor"
    return {year: {"days_built": value["days"], "rows": value["rows"],
                   "disagreement_share": value["disagreements"] / value["rows"] if value["rows"] else None,
                   "raw_basis_share": value["raw"] / value["rows"] if value["rows"] else None}
            for year, value in report.items()}


def replay(database: Any, slot: str, start: date, end: date, *, apply: bool = False) -> dict[str, Any]:
    if slot not in {"close", "auction"}:
        raise ValueError("slot must be close or auction")
    sessions = _read(database, tactic_trial_repository.read_open_sessions, start, end)
    chunks = [sessions[index:index + tactic_trial_repository.CHUNK_SESSIONS]
              for index in range(0, len(sessions), tactic_trial_repository.CHUNK_SESSIONS)]
    built: list[Mapping[str, Any]] = []
    chunk_documents: list[list[Mapping[str, Any]]] = []
    for chunk in chunks:
        first, last = chunk[0], chunk[-1]
        with database.transaction() as connection:
            existing = tactic_trial_repository.read_capability(
                connection, CLOSE_CAPABILITY if slot == "close" else AUCTION_CAPABILITY, first, last,
            )
            tactic_trial_repository.read_capability(connection, SETTLED_CAPABILITY, first, last)
        if slot == "close":
            lists = _read(database, tactic_trial_repository.read_limit_lists, first, last)
            risk_rows = _read(database, tactic_trial_repository.read_capability, RISK_CAPABILITY, first, last)
            risk = {str(item["trade_date"]): item for item in risk_rows}
            symbols = [str(row[0]) for item in lists["days"] for values in item["lists"].values()
                       for row in values if isinstance(row, list) and row]
            if symbols:
                with database.transaction() as connection:
                    bar_rows = tactic_trial_repository.read_day_bars(connection, first, last, symbols)
                    window_rows = tactic_trial_repository.read_history_windows(
                        connection, first - timedelta(days=400), last,
                        [(symbol, day) for day in chunk for symbol in symbols],
                    )
                    turnover = tactic_trial_repository.read_market_turnover(connection, first - timedelta(days=120), last)
            else:
                bar_rows, window_rows, turnover = [], [], []
            windows: dict[tuple[str, date], list[Mapping[str, Any]]] = {}
            for row in window_rows:
                windows.setdefault((str(row["symbol"]), row["pick_day"]), []).append(row)
            documents = []
            for item in lists["days"]:
                day = item["trade_date"]
                day_bars = {str(row["symbol"]): row for row in bar_rows if row["trading_date"] == day}
                with database.transaction() as connection:
                    reading = _close_reading(
                        connection, day, item["lists"], risk.get(day.isoformat(), {}), replayed=True,
                        preloaded_bars=day_bars, preloaded_windows=windows, preloaded_turnover=turnover,
                        preloaded_previous=[prior for prior in built if prior["slot"] == "close"
                                            and prior["trade_date"] < day.isoformat()][-3:],
                    )
                built.append(reading)
                documents.append(reading)
        else:
            auction_rows = _read(database, tactic_trial_repository.read_auction_lists, first, last)
            by_day = {str(item["trade_date"]): item for item in auction_rows}
            symbols = [row["symbol"] for item in auction_rows for row in item.get("rows", []) if row.get("symbol")]
            with database.transaction() as connection:
                bar_rows = tactic_trial_repository.read_day_bars(connection, first, last, symbols)
            bars_by_day = {}
            for row in bar_rows:
                bars_by_day.setdefault(str(row["trading_date"]), {})[str(row["symbol"])] = row
            documents = []
            for day in chunk:
                auction = by_day.get(day.isoformat(), {"rows": []})
                desk_rows = []
                for position, item in enumerate(auction.get("rows", [])):
                    bar = bars_by_day.get(day.isoformat(), {}).get(item.get("symbol"))
                    if bar is None:
                        continue
                    open_price, pre_close, limit_up = bar.get("open"), bar.get("pre_close"), bar.get("limit_up")
                    gap = None if open_price is None or pre_close in (None, 0) else open_price / pre_close - 1
                    desk_rows.append({"symbol": item["symbol"], "name": None, "open": open_price, "gap": gap,
                        "opens_at_limit": None if open_price is None or limit_up is None else open_price >= limit_up,
                        "bucket": None, "boards": None, "amount": item.get("amount"), "volume": None,
                        "auction_amount_rank": position + 1, "sealed_from_open": None})
                if not desk_rows:
                    continue
                previous = next((item for item in reversed(built) if item["slot"] == "auction"), None)
                previous_rows = {row["symbol"]: row for row in (previous or {}).get("rows", [])}
                rows = [build_auction_row(item, desk_rows, previous_rows.get(item["symbol"])) for item in desk_rows]
                for row in rows:
                    row["reference_close"] = (bars_by_day.get(day.isoformat(), {}).get(row["symbol"]) or {}).get("pre_close")
                evaluated = evaluate_conditions(rows, {"flags": [], "unknown": []}, "auction")
                reading = _research({"trade_date": day.isoformat(), "slot": "auction", "version": VERSION,
                    "decided_at": session_close(day).isoformat(), "day": evaluated["day"], "rows": evaluated["rows"],
                    "complete": True, "replayed": True, "source": "bars+auction_list"})
                built.append(reading)
                documents.append(reading)
        chunk_documents.append(documents)
    report = _year_report(built, {})
    refused = next((year for year, value in report.items()
                    if value["disagreement_share"] is not None and value["disagreement_share"] > REPLAY_DISAGREEMENT_LIMIT), None)
    if apply and refused is not None:
        return _research({"status": "refused", "reason": "limit_membership_disagreement", "year": refused, "years": report})
    if apply:
        capability = CLOSE_CAPABILITY if slot == "close" else AUCTION_CAPABILITY
        for documents in chunk_documents:
            if documents:
                derived_daily_readings.store(database, capability, documents)
    return _research({"status": "completed", "slot": slot, "start": start.isoformat(), "end": end.isoformat(),
                      "years": report, "days": len(built), "rows": sum(len(item.get("rows", [])) for item in built), "replayed": True})


def board(database: Any, day: date) -> dict[str, Any]:
    with database.transaction() as connection:
        reading = tactic_trial_repository.read_capability(connection, AUCTION_CAPABILITY, day, day)
    return _research({"trade_date": day.isoformat(), "reading": reading[-1] if reading else None})


def summary(database: Any, day: date) -> dict[str, Any]:
    with database.transaction() as connection:
        reading = tactic_trial_repository.read_capability(connection, SUMMARY_CAPABILITY, day, day)
    return _research({"trade_date": day.isoformat(), "summary": reading[-1] if reading else None})


def cases(database: Any, tactic_key: str, days: int) -> dict[str, Any]:
    with database.transaction() as connection:
        readings = tactic_trial_repository.read_capability(connection, SUMMARY_CAPABILITY, date.today() - timedelta(days=days), date.today())
    return _research({"tactic": tactic_key, "days": readings[-days:]})


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


__all__ = ["board", "cases", "refresh_auction", "refresh_close", "replay", "settle", "summarise"]
