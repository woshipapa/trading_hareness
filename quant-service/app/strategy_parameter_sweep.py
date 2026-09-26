"""What would have happened at a different threshold, on the day's own inputs.

The outcome review can say a condition blocked a name that then ran; it
cannot say whether loosening it would have helped, because loosening also
lets through the names that went nowhere.  Both sides have to be counted,
and both are already recorded: every scan's inputs sit in
``quant.intraday_rule_input_snapshots``.

So a variant is not an opinion.  Replay the same pure rule over the same
frozen inputs with one threshold changed, take the first scan that would
have entered, and measure that entry exactly as the live one is measured -
net of a round trip, against the day's median, and with a fill at the limit
counted as unbuyable rather than as a win.

Deliberately not a backtest: no bar is re-simulated, no position is sized,
and nothing outside the recorded scan window is imagined.  A variant that
looks better here has earned a preregistered change (see
``strategy_change_log``), not an edit.

Research only, and never run against live defaults: the thresholds are
passed in, so the running scan's own table is untouched.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .strategy_outcome_measures import measure
from .teacher_review_playbooks import DEFAULTS
from .teacher_review_rules import (
    SnapshotTape,
    active_plan,
    evaluate,
    missing_inputs,
    scan_features,
)

#: The thresholds the learning layer actually flags, with the values worth
#: asking about.  Prices are never swept - they are the teacher's words.
GRID: dict[str, tuple[Any, ...]] = {
    "vol_ratio_min": (1.0, 1.2, 1.5, 1.8),
    "minute_volume_multiple_min": (1.5, 2.0, 2.5),
    "not_falling_return_5m_min": (-1.0, -0.6, -0.3, 0.0),
}
#: A variant is only reported when this many names took part, so one lucky
#: name cannot recommend a change.
MIN_SYMBOLS = 3


def variants(grid: Mapping[str, Sequence[Any]] | None = None,
             base: Mapping[str, Any] = DEFAULTS) -> list[dict[str, Any]]:
    """One-at-a-time variants: each changes a single threshold from the base.

    A full cross-product would multiply the replay cost and, at the sample
    sizes this system produces, would mostly find noise.
    """
    out: list[dict[str, Any]] = [{"label": "live", "overrides": {}, "defaults": dict(base)}]
    for name, values in (grid or GRID).items():
        for value in values:
            if base.get(name) == value:
                continue
            out.append({"label": f"{name}={value}", "overrides": {name: value},
                        "defaults": {**base, name: value}})
    return out


def first_entry(symbol: str, name: str, rows: Sequence[Mapping[str, Any]],
                defaults: Mapping[str, Any]) -> dict[str, Any] | None:
    """The first scan that would have entered under ``defaults``.

    Mirrors the live scan exactly: the tape is rebuilt from the same samples
    and a missing required input still refuses the entry.
    """
    tape = SnapshotTape()
    for row in rows:
        observed_at = row["observed_at"]
        payload = row.get("inputs") or {}
        watch = payload.get("watch")
        if not isinstance(watch, Mapping):
            continue
        plan = active_plan(watch, observed_at)
        quote = payload.get("quote")
        if plan is None or not isinstance(quote, Mapping) or quote.get("price") in (None, ""):
            continue
        minute = payload.get("minute_features") if isinstance(payload.get("minute_features"), Mapping) else None
        previous = payload.get("previous_quote") if isinstance(payload.get("previous_quote"), Mapping) else None
        try:
            features = scan_features(symbol, quote, minute, observed_at, name, previous, defaults=defaults)
            tape.observe(symbol, observed_at, features.get("price"), features.get("volume_lot"),
                         features["sources"].get("volume_lot"))
            view = tape.features(symbol, observed_at)
            if view:
                features = scan_features(symbol, quote, minute, observed_at, name, previous, view, defaults=defaults)
            result = evaluate(plan, features, defaults=defaults)
            if result["action"] != "entry" or missing_inputs(str(plan["playbook"]), features):
                continue
        except Exception:  # noqa: BLE001 - one unreplayable scan must not end the sweep
            continue
        return {"at": observed_at, "price": features.get("price"), "sealed": features.get("sealed"),
                "path": result.get("path"), "playbook": str(plan["playbook"])}
    return None


def sweep_symbol(symbol: str, name: str, rows: Sequence[Mapping[str, Any]], *,
                 bar: Mapping[str, Any] | None, next_bar: Mapping[str, Any] | None = None,
                 benchmark_pct: float | None = None,
                 grid: Mapping[str, Sequence[Any]] | None = None) -> list[dict[str, Any]]:
    """Every variant's entry for one name, measured the same way as the live one."""
    results = []
    for variant in variants(grid):
        entry = first_entry(symbol, name, rows, variant["defaults"])
        measured = measure(entry, bar, next_bar, benchmark_pct=benchmark_pct) if entry else {}
        results.append({
            "label": variant["label"], "overrides": variant["overrides"],
            "entered": entry is not None,
            "entry": None if not entry else {"at": entry["at"], "price": entry["price"],
                                             "sealed": entry.get("sealed")},
            "measures": measured or None,
        })
    return results


def summarize(per_symbol: Mapping[str, Sequence[Mapping[str, Any]]], *,
              min_symbols: int = MIN_SYMBOLS) -> list[dict[str, Any]]:
    """Aggregate one session's variants: what each one caught, and what it cost.

    ``entered`` counts every name the variant would have flagged; the returns
    average only the ones an account could have taken, because an entry at
    the limit is not a result.
    """
    totals: dict[str, dict[str, Any]] = {}
    for symbol, results in per_symbol.items():
        for item in results:
            row = totals.setdefault(item["label"], {
                "label": item["label"], "overrides": item["overrides"], "symbols": 0,
                "entered": 0, "unbuyable": 0, "net": [], "excess": [], "names": [],
            })
            row["symbols"] += 1
            if not item["entered"]:
                continue
            row["entered"] += 1
            measures = item.get("measures") or {}
            if measures.get("evaluable") is False:
                row["unbuyable"] += 1
                continue
            if measures.get("net_session_return_pct") is not None:
                row["net"].append(float(measures["net_session_return_pct"]))
            if measures.get("excess_session_pct") is not None:
                row["excess"].append(float(measures["excess_session_pct"]))
            row["names"].append(symbol)

    def mean(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 3) if values else None

    rows = []
    for row in totals.values():
        if row["symbols"] < min_symbols:
            continue
        taken = len(row["net"])
        rows.append({
            "label": row["label"], "overrides": row["overrides"], "symbols": row["symbols"],
            "entered": row["entered"], "unbuyable": row["unbuyable"], "evaluable_entries": taken,
            "net_mean_pct": mean(row["net"]), "excess_mean_pct": mean(row["excess"]),
            "win_rate_pct": (round(sum(1 for value in row["net"] if value > 0) / taken * 100, 1)
                             if taken else None),
            "names": sorted(row["names"])[:12],
        })
    live = next((row for row in rows if row["label"] == "live"), None)
    for row in rows:
        row["vs_live_entries"] = None if live is None else row["entered"] - live["entered"]
        row["vs_live_net_mean_pct"] = (
            None if live is None or row["net_mean_pct"] is None or live["net_mean_pct"] is None
            else round(row["net_mean_pct"] - live["net_mean_pct"], 3))
    rows.sort(key=lambda item: (item["label"] != "live", -(item["excess_mean_pct"] or -99)))
    return rows


def sweep_lines(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Report lines: what each variant would have caught and kept."""
    lines = []
    for row in rows:
        delta = "" if row["vs_live_entries"] in (None, 0) else f"（较现状 {row['vs_live_entries']:+d} 只）"
        net = "—" if row["net_mean_pct"] is None else f"{row['net_mean_pct']:+.2f}%"
        excess_pct = "—" if row["excess_mean_pct"] is None else f"{row['excess_mean_pct']:+.2f}%"
        win = "—" if row["win_rate_pct"] is None else f"{row['win_rate_pct']:.0f}%"
        lines.append(f"{row['label']}：触发 {row['entered']}/{row['symbols']} 只{delta}，"
                     f"其中买不到 {row['unbuyable']} 只，可成交 {row['evaluable_entries']} 只｜"
                     f"净收益均值 {net}｜超额 {excess_pct}｜胜率 {win}")
    return lines


__all__ = [
    "GRID", "MIN_SYMBOLS", "first_entry", "summarize", "sweep_lines", "sweep_symbol", "variants",
]
