"""Pure tactic definitions and daily evidence comparisons."""

from __future__ import annotations

import json
from array import array
from collections import deque
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256
from math import erfc, sqrt
from statistics import mean, stdev
from typing import Any, Mapping, Sequence

from .research_trials import bh_q_values
from .tactic_conditions import CONDITIONS
from .tactic_exits import EXITS

REGISTERED_ON = date(2026, 10, 13)
STATE_OUTCOMES = ("seal", "big_loss", "open_to_close")


@dataclass(frozen=True)
class Tactic:
    key: str
    name: str
    slot: str
    side: str
    source: str
    all_of: tuple[str, ...] = ()
    none_of: tuple[str, ...] = ()
    at_least: tuple[int, tuple[str, ...]] | None = None
    day_conditions: tuple[str, ...] = ()
    cell: tuple[str, ...] = ()
    entry: str = ""
    exit: str = ""
    primary: str = ""
    expects: int = 1
    registered_on: date = REGISTERED_ON

    @property
    def combination(self) -> bool:
        """A registered combination, as opposed to the single-condition tactic every stock condition gets."""
        return not self.key.startswith("only.")


def _combo(
    key: str, name: str, slot: str, side: str, *, all_of: Sequence[str] = (), none_of: Sequence[str] = (),
    at_least: tuple[int, Sequence[str]] | None = None, day_conditions: Sequence[str] = (), primary: str,
    expects: int, cell: Sequence[str] | None = None, exit_key: str | None = None,
) -> Tactic:
    entry = "held" if side == "hold" or (side == "avoid" and slot == "close") else "buy_open"
    return Tactic(
        key=key, name=name, slot=slot, side=side, source="plan:v1",
        all_of=tuple(all_of), none_of=tuple(none_of),
        at_least=None if at_least is None else (at_least[0], tuple(at_least[1])),
        day_conditions=tuple(day_conditions),
        cell=tuple(cell or (("board_bucket",) if slot == "close" else ("board_bucket", "gap_bucket"))),
        entry=entry, exit=exit_key or ("gap_rule" if side in {"focus", "hold"} else "close"),
        primary=primary, expects=expects,
    )


_COMBINATIONS = (
    _combo("leader_hold", "龙头持有", "close", "hold", all_of=("theme_7plus", "theme_leader_earliest"), none_of=("seal_small", "volume_heavy", "seal_late"), primary="held:gap_rule", expects=1),
    _combo("relay_2_3", "二三板接力", "close", "focus", all_of=("board_2_3", "theme_7plus"), none_of=("seal_small", "volume_heavy"), primary="seal", expects=1),
    _combo("far_volume_cleared", "远端放量清除", "close", "focus", all_of=("amount_record_250", "theme_big"), primary="buy_open:gap_rule", expects=1),
    _combo("shrunk_acceleration", "缩量加速", "close", "focus", all_of=("volume_shrunk",), none_of=("sealed_from_open",), at_least=(1, ("board_2_3", "board_4plus")), primary="seal", expects=1),
    _combo("ladder_env_relay", "梯队环境接力", "close", "focus", at_least=(1, ("board_2_3", "board_4plus")), day_conditions=("env_ladder_ok",), primary="buy_open:gap_rule", expects=1),
    _combo("after_top_failed", "最高板断板后", "close", "avoid", at_least=(1, ("board_2_3", "board_4plus")), day_conditions=("env_top_board_failed",), primary="held:close", expects=-1),
    _combo("weak_board_two_signs", "弱板两项", "close", "avoid", at_least=(2, ("seal_small", "volume_heavy", "amount_record_250", "seal_late", "runup_40", "risk_top_fifth", "theme_lone")), primary="big_loss", expects=1),
    _combo("tier_race_winner", "梯队竞速胜者", "close", "hold", all_of=("tier_first",), none_of=("seal_small", "volume_heavy"), primary="held:gap_rule", expects=1),
    _combo("kawei_hold", "卡位持有", "close", "hold", all_of=("theme_7plus",), at_least=(1, ("front_row", "kawei_leader")), primary="seal", expects=1),
    _combo("assist_no_relay", "助攻不接力", "close", "avoid", none_of=("tier_first", "front_row"), at_least=(1, ("assist", "back_row")), primary="seal", expects=-1),
    _combo("catch_up_low", "低位补涨", "close", "focus", all_of=("catch_up_first_board",), none_of=("runup_40", "seal_small", "seal_late"), primary="buy_open:gap_rule", expects=1),
    _combo("first_open_after_one_word", "一字后首次打开", "close", "avoid", all_of=("consensus_to_divergence",), primary="big_loss", expects=1),
    _combo("rebound_board_risk", "反包风险", "close", "avoid", all_of=("rebound_board",), primary="big_loss", expects=1),
    _combo("kawei_gap_up", "卡位高开", "auction", "avoid", all_of=("gap_up_5_not_sealed",), at_least=(1, ("prev.front_row", "prev.kawei_leader")), primary="buy_open:close", expects=-1, cell=("board_bucket",)),
    _combo("tier_winner_moderate_gap", "梯队胜者温和高开", "auction", "focus", all_of=("prev.tier_first", "gap_0_to_5"), primary="buy_open:gap_rule", expects=1, cell=("board_bucket",)),
    _combo("leader_moderate_gap", "龙头温和高开", "auction", "focus", all_of=("prev.theme_leader_earliest", "prev.theme_7plus", "gap_0_to_5"), none_of=("prev.risk_top_fifth",), primary="buy_open:gap_rule", expects=1, cell=("board_bucket",)),
    _combo("teacher_auction_entry", "老师竞价入场", "auction", "focus", all_of=("gap_3_to_8", "auction_burst"), none_of=("prev.sealed_from_open",), primary="buy_open:must_rise", expects=1, cell=("board_bucket",), exit_key="must_rise"),
    _combo("amount_top5_big_theme", "强题材竞价前五", "auction", "focus", all_of=("auction_amount_top5", "prev.theme_big"), primary="seal", expects=1),
    _combo("gap_up_weak_board", "弱板高开", "auction", "avoid", all_of=("gap_up_5_not_sealed",), at_least=(1, ("prev.seal_small", "prev.volume_heavy")), primary="open_to_close", expects=-1, cell=("board_bucket",)),
    _combo("weak_board_gap_down", "弱板低开修复", "auction", "focus", all_of=("gap_down_2",), at_least=(1, ("prev.seal_small", "prev.volume_heavy")), primary="buy_open:gap_rule", expects=1, cell=("board_bucket",)),
)


def _return_side(outcome: str, claim: int) -> str:
    if claim == -1:
        return "avoid"
    if outcome == "big_loss":
        return "avoid"
    if outcome == "buy_open:close":
        return "focus"
    return "hold"


def _single_condition_tactics() -> tuple[Tactic, ...]:
    tactics = []
    for condition in CONDITIONS:
        if condition.level != "stock":
            continue
        outcome = condition.claim_outcome
        tactics.append(Tactic(
            key=f"only.{condition.key}", name=condition.name, slot=condition.slot,
            side=_return_side(outcome, condition.claim), source=condition.source,
            all_of=(condition.key,), cell=("board_bucket",),
            entry="held" if condition.slot == "close" else "buy_open", exit="close",
            primary=outcome, expects=condition.claim,
        ))
    return tuple(tactics)


TACTICS = _COMBINATIONS + _single_condition_tactics()


def _condition_value(key: str, row: Mapping[str, Any], day: Mapping[str, Any], *, day_level: bool = False) -> bool | None:
    if key.startswith("prev.") and row.get("prev") is None:
        return None
    flags = day.get("flags", ()) if day_level else row.get("flags", ())
    unknown = day.get("unknown", ()) if day_level else row.get("unknown", ())
    if key in flags:
        return True
    if key in unknown:
        return None
    return False


def _combine(values: Sequence[bool | None]) -> bool | None:
    return False if any(value is False for value in values) else None if any(value is None for value in values) else True


def _at_least(value: tuple[int, Sequence[str]] | None, row: Mapping[str, Any], day: Mapping[str, Any]) -> bool | None:
    if value is None:
        return True
    minimum, keys = value
    states = [_condition_value(key, row, day) for key in keys]
    true_count = sum(state is True for state in states)
    unknown_count = sum(state is None for state in states)
    if true_count >= minimum:
        return True
    if true_count + unknown_count < minimum:
        return False
    return None


def evaluate_tactic(tactic: Tactic, row: Mapping[str, Any], day: Mapping[str, Any]) -> bool | None:
    """Return whether a stored row is a pick, not a pick, or unknown."""
    all_values = [_condition_value(key, row, day) for key in tactic.all_of]
    none_values = [_condition_value(key, row, day) for key in tactic.none_of]
    if any(value is True for value in none_values):
        none_value: bool | None = False
    elif any(value is None for value in none_values):
        none_value = None
    else:
        none_value = True
    return _combine([*all_values, none_value, _at_least(tactic.at_least, row, day), *(
        _condition_value(key, row, day, day_level=True) for key in tactic.day_conditions
    )])


def _mean(values: Sequence[Decimal | float | int | None]) -> Decimal | None:
    valid = [_number(value) for value in values if value is not None]
    return None if not valid else sum(valid, Decimal(0)) / len(valid)


def _number(value: Any) -> Decimal:
    return Decimal(int(value)) if isinstance(value, bool) else Decimal(str(value))


def _cell(row: Mapping[str, Any], fields: Sequence[str]) -> tuple[Any, ...]:
    return tuple(row.get(field) for field in fields)


def _outcome_value(result: Mapping[str, Any], outcome: str) -> tuple[Any, str | None]:
    if ":" in outcome:
        entry, exit_key = outcome.split(":", 1)
        item = result.get("entries", {}).get(entry, {}).get(exit_key)
        if item is None:
            return None, None
        value = item.get("net_return") if entry == "buy_open" else item.get("raw_return")
        return value, item.get("status")
    return result.get(outcome), None


def _outcome_keys() -> tuple[str, ...]:
    returns = tuple(f"{entry}:{exit_.key}" for entry in ("held", "buy_open") for exit_ in EXITS)
    return returns + STATE_OUTCOMES


def _summary(
    picks: Sequence[Mapping[str, Any]], comparisons: Sequence[Sequence[Mapping[str, Any]]],
    outcomes: Mapping[str, Mapping[str, Any]], outcome: str,
) -> dict[str, Any]:
    pick_values: list[Any] = []
    comparison_means: list[Decimal | None] = []
    differences: list[Decimal] = []
    comparison_values_missing = 0
    comparison_values_count = 0
    for row, comparison_rows in zip(picks, comparisons):
        value, _status = _outcome_value(outcomes.get(row["symbol"], {}), outcome)
        pick_values.append(value)
        comparison_values = [_outcome_value(outcomes.get(item["symbol"], {}), outcome)[0] for item in comparison_rows]
        comparison_values_count += sum(value is not None for value in comparison_values)
        comparison_values_missing += sum(value is None for value in comparison_values)
        comparison_mean = _mean(comparison_values)
        comparison_means.append(comparison_mean)
        if value is not None and comparison_mean is not None:
            differences.append(_number(value) - comparison_mean)
    return {
        "pick_mean": _mean(pick_values),
        "comparison_mean": _mean(comparison_means),
        "cell_means": [
            {"symbol": row["symbol"], "mean": mean, "rows": len(group)}
            for row, mean, group in zip(picks, comparison_means, comparisons)
        ],
        "difference": _mean(differences),
        "pick_values": sum(value is not None for value in pick_values),
        "pick_values_missing": sum(value is None for value in pick_values),
        "comparison_values": comparison_values_count,
        "comparison_values_missing": comparison_values_missing,
        "differences": len(differences),
    }


def day_result(
    tactic: Tactic, rows: Sequence[Mapping[str, Any]], day: Mapping[str, Any],
    outcomes: Mapping[str, Mapping[str, Any]], keys: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Score one tactic's picks against non-picked rows in the same cells, on every outcome or on ``keys``."""
    statuses = [(row, evaluate_tactic(tactic, row, day)) for row in rows]
    picks = [row for row, value in statuses if value is True]
    unknown = [row for row, value in statuses if value is None]
    comparisons = [
        [candidate for candidate, value in statuses if value is False and _cell(candidate, tactic.cell) == _cell(row, tactic.cell)]
        for row in picks
    ]
    counts = {
        "picks": len(picks), "picks_without_comparison_row": sum(not group for group in comparisons),
        "rows_unknown": len(unknown), "picks_no_trade": 0, "picks_not_resolved": 0,
    }
    primary_status = f"{tactic.entry}:{tactic.exit}"
    for row in picks:
        _value, status = _outcome_value(outcomes.get(row["symbol"], {}), primary_status)
        counts["picks_no_trade"] += status == "no_trade"
        counts["picks_not_resolved"] += status == "not_resolved"
    scored = {key: _summary(picks, comparisons, outcomes, key) for key in keys or _outcome_keys()}
    return {"primary": scored[tactic.primary], "outcomes": scored, "counts": counts}


def _date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def json_safe(value: Any) -> Any:
    """The value as plain JSON types: what a stored document must be (the store's adapter takes no Decimal)."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def _cached_days(days: Sequence[Mapping[str, Any]], tactics: Sequence[Tactic]) -> list[dict[str, Any]]:
    cached = []
    for day in days:
        results = {
            tactic.key: day_result(tactic, day["rows"], day.get("day", {}), day.get("outcomes", {}))
            for tactic in tactics if tactic.slot == day["slot"]
        }
        cached.append({"source": day, "date": _date(day["trade_date"]), "results": results})
    return cached


def _record_outcomes(tactic: Tactic) -> tuple[str, ...]:
    """The outcomes a tactic's records are kept for: every one for a combination, three for a single condition."""
    if tactic.combination:
        return _outcome_keys()
    return tuple(dict.fromkeys((tactic.primary, f"{tactic.entry}:{tactic.exit}", f"{tactic.entry}:close")))


class _Series:
    """One tactic, one outcome, one period: what its record needs, without the days themselves."""

    def __init__(self) -> None:
        self.days_with_picks = 0
        self.picks = 0
        self.pick_means = array("d")
        self.differences = array("d")

    def add(self, picks: int, pick_mean: Any, difference: Any) -> None:
        self.picks += picks
        if picks > 0:
            self.days_with_picks += 1
            if pick_mean is not None:
                self.pick_means.append(float(pick_mean))
        if difference is not None:
            self.differences.append(float(difference))

    def record(self, *, in_sample: bool = False) -> dict[str, Any]:
        differences = list(self.differences)
        daily_difference = mean(differences) if differences else None
        t_value = None
        if len(differences) >= 3:
            deviation = stdev(differences)
            t_value = None if deviation == 0 else daily_difference / (deviation / sqrt(len(differences)))
        return {
            "days_with_picks": self.days_with_picks, "picks": self.picks,
            "mean_daily_picks_mean": mean(self.pick_means) if self.pick_means else None,
            "mean_daily_difference": daily_difference, "t": t_value,
            "positive_share": (sum(value > 0 for value in differences) / len(differences)) if differences else None,
            "in_sample": in_sample,
        }


def _label(record: Mapping[str, Any], expects: int) -> str:
    if record["days_with_picks"] < 20 or record["picks"] < 60:
        return "collecting"
    t_value = record["t"]
    if t_value is None:
        return "undecided"
    directed = t_value * expects
    return "supports" if directed >= 2 else "against" if directed <= -2 else "undecided"


def _sell_cell(values: Sequence[Any]) -> dict[str, Any]:
    valid = [_number(value) for value in values if value is not None]
    return {"sum": sum(valid, Decimal(0)), "count": len(valid), "missing": len(values) - len(valid)}


def _sell_sums(rows: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]]) -> dict[str, Any]:
    return {
        "rows": len(rows),
        "returns": {entry: {exit_.key: _sell_cell([_outcome_value(outcome, f"{entry}:{exit_.key}")[0] for _row, outcome in rows])
                            for exit_ in EXITS} for entry in ("held", "buy_open")},
        "room": {key: _sell_cell([outcome.get(key) for _row, outcome in rows]) for key in ("s1_high", "s3_high", "s1_low")},
    }


def _sell_scope_sums(day: Mapping[str, Any]) -> dict[str, Any]:
    rows = [(row, day.get("outcomes", {}).get(row["symbol"], {})) for row in day["rows"]]

    def by(field: str) -> dict[str, Any]:
        buckets = sorted({row.get(field) for row, _outcome in rows}, key=lambda value: str(value))
        return {str(bucket): _sell_sums([(row, outcome) for row, outcome in rows if row.get(field) == bucket]) for bucket in buckets}

    result = {"all": _sell_sums(rows), "board_bucket": by("board_bucket")}
    if day["slot"] == "auction":
        result["gap_bucket"] = by("gap_bucket")
    return result


def _add_sell(total: dict[str, Any], part: Mapping[str, Any]) -> dict[str, Any]:
    """Add one day's sell sums (a group, or a mapping of groups) into the running total."""
    for key, value in part.items():
        if key in ("sum", "count", "missing", "rows"):
            total[key] = total.get(key, 0) + (_number(value) if key == "sum" else value)
        else:
            _add_sell(total.setdefault(key, {}), value)
    return total


def _sell_means(sums: Mapping[str, Any]) -> dict[str, Any]:
    """Turn sums into the means the summary shows."""
    if "sum" in sums:
        return {"mean": sums["sum"] / sums["count"] if sums["count"] else None, "count": sums["count"], "missing": sums["missing"]}
    return {key: value if key == "rows" else _sell_means(value) for key, value in sums.items()}


def _seal_counts(day: Mapping[str, Any]) -> dict[str, list[int]]:
    counts: dict[str, list[int]] = {}
    for row in day["rows"]:
        value = day.get("outcomes", {}).get(row["symbol"], {}).get("seal")
        if value is not None:
            cell = counts.setdefault(str(row.get("board_bucket")), [0, 0])
            cell[0] += bool(value)
            cell[1] += 1
    return counts


def registry_fingerprint(tactics: Sequence[Tactic] = TACTICS) -> str:
    """What a stored digest was computed with: the tactics' definitions, the outcomes kept for each, the sell rules.

    A condition's own rule is code and is not in it: changing a threshold needs a new reading version.
    """
    definition = [(tactic.key, tactic.slot, tactic.all_of, tactic.none_of, tactic.at_least, tactic.day_conditions,
                   tactic.cell, tactic.entry, tactic.exit, tactic.primary, _record_outcomes(tactic)) for tactic in tactics]
    return sha256(json.dumps([definition, [exit_.key for exit_ in EXITS]], sort_keys=True).encode()).hexdigest()[:12]


def _resolved(outcome: Mapping[str, Any]) -> bool:
    return any(item.get("status") == "settled" for exits in outcome.get("entries", {}).values() for item in exits.values())


def day_digest(day: Mapping[str, Any], tactics: Sequence[Tactic] = TACTICS) -> dict[str, Any]:
    """What the records, the switch table, the sell points and the sealing rates need from one settled day.

    A few tens of kilobytes instead of the day's rows and every outcome of every row: the evening summary folds
    these one by one, so its memory does not grow with the history.
    """
    slot, rows, outcomes = day["slot"], day["rows"], day.get("outcomes", {})
    results = {}
    for tactic in tactics:
        if tactic.slot != slot:
            continue
        result = day_result(tactic, rows, day.get("day", {}), outcomes, _record_outcomes(tactic))
        results[tactic.key] = {"picks": result["counts"]["picks"], "outcomes": {
            key: [item["pick_mean"], item["difference"]] for key, item in result["outcomes"].items()}}
    digest = {"trade_date": _date(day["trade_date"]), "slot": slot, "registry": registry_fingerprint(tactics),
              "rows": len(rows), "resolved": sum(_resolved(outcomes.get(row["symbol"], {})) for row in rows),
              "tactics": results, "sell": _sell_scope_sums(day), "seal": _seal_counts(day)}
    if slot == "close":
        digest["switch"] = {
            "daily": _mean([_outcome_value(outcomes.get(row["symbol"], {}), "held:close")[0] for row in rows]),
            "flags": list(day.get("day", {}).get("flags", ())), "unknown": list(day.get("day", {}).get("unknown", ())),
        }
    return json_safe(digest)


class History:
    """The settled days folded one digest at a time, oldest first."""

    def __init__(self, tactics: Sequence[Tactic] = TACTICS) -> None:
        self.tactics = tuple(tactics)
        self._series = {(tactic.key, outcome, period): _Series()
                        for tactic in self.tactics for outcome in _record_outcomes(tactic) for period in ("replay", "forward")}
        self._recent: dict[str, deque] = {tactic.key: deque(maxlen=60) for tactic in self.tactics}
        self._switch: dict[str, list[tuple[bool, float]]] = {
            condition.key: [] for condition in CONDITIONS if condition.level == "day" and condition.slot == "close"}
        self.sell: dict[str, dict[str, Any]] = {"close": {}, "auction": {}}
        self.seal: dict[str, dict[str, list[int]]] = {"close": {}, "auction": {}}
        self.rows = {"close": 0, "auction": 0}

    def add(self, digest: Mapping[str, Any]) -> None:
        slot, day = digest["slot"], _date(digest["trade_date"])
        self.rows[slot] += digest["rows"]
        for tactic in self.tactics:
            if tactic.slot != slot:
                continue
            result = digest["tactics"][tactic.key]
            forward = day >= tactic.registered_on
            for outcome in _record_outcomes(tactic):
                self._series[tactic.key, outcome, "forward" if forward else "replay"].add(result["picks"], *result["outcomes"][outcome])
            if forward:
                self._recent[tactic.key].append(result)
        _add_sell(self.sell[slot], digest["sell"])
        for bucket, (sealed, count) in digest["seal"].items():
            cell = self.seal[slot].setdefault(bucket, [0, 0])
            cell[0] += sealed
            cell[1] += count
        switch = digest.get("switch")
        if switch is not None and switch["daily"] is not None:
            for key, samples in self._switch.items():
                if key in switch["flags"]:
                    samples.append((True, float(switch["daily"])))
                elif key not in switch["unknown"]:
                    samples.append((False, float(switch["daily"])))

    def records(self) -> dict[str, Any]:
        records: dict[str, Any] = {}
        for tactic in self.tactics:
            outcomes = {}
            for outcome in sorted(_record_outcomes(tactic)):
                recent = _Series()
                for result in self._recent[tactic.key]:
                    recent.add(result["picks"], *result["outcomes"][outcome])
                outcomes[outcome] = {
                    "replay": self._series[tactic.key, outcome, "replay"].record(in_sample=True),
                    "forward": self._series[tactic.key, outcome, "forward"].record(), "recent": recent.record(),
                }
            records[tactic.key] = {"name": tactic.name, "slot": tactic.slot, "primary": tactic.primary,
                                   "expects": tactic.expects, "outcomes": outcomes}
        p_values: dict[str, float | None] = {}
        for tactic in self.tactics:
            forward = records[tactic.key]["outcomes"][tactic.primary]["forward"]
            if _label(forward, tactic.expects) == "collecting" or forward["t"] is None:
                p_values[tactic.key] = None
            else:
                # The normal approximation is sufficient for this descriptive research label.
                p_values[tactic.key] = erfc(abs(float(forward["t"])) / sqrt(2))
        q_values = bh_q_values(p_values)
        for tactic in self.tactics:
            primary = records[tactic.key]["outcomes"][tactic.primary]["forward"]
            records[tactic.key]["label"] = _label(primary, tactic.expects)
            records[tactic.key]["q"] = q_values[tactic.key]
        return json_safe(records)

    def switch_records(self) -> dict[str, Any]:
        result = {}
        for key, samples in self._switch.items():
            true_values = [value for state, value in samples if state]
            false_values = [value for state, value in samples if not state]
            difference = t_value = None
            if len(true_values) >= 3 and len(false_values) >= 3:
                difference = mean(true_values) - mean(false_values)
                true_variance = sum((value - mean(true_values)) ** 2 for value in true_values) / (len(true_values) - 1)
                false_variance = sum((value - mean(false_values)) ** 2 for value in false_values) / (len(false_values) - 1)
                denominator = sqrt(true_variance / len(true_values) + false_variance / len(false_values))
                t_value = None if denominator == 0 else difference / denominator
            result[key] = json_safe({
                "true_days": len(true_values), "false_days": len(false_values),
                "true_mean": _mean(true_values), "false_mean": _mean(false_values),
                "difference": difference, "t": t_value,
            })
        return result

    def exit_tables(self, slot: str) -> dict[str, Any]:
        result = {}
        for tactic in self.tactics:
            if tactic.slot != slot or not tactic.combination:
                continue
            exits = {}
            for exit_ in EXITS:
                values = [value for period in ("replay", "forward")
                          for value in self._series[tactic.key, f"{tactic.entry}:{exit_.key}", period].pick_means]
                exits[exit_.key] = json_safe({"key": exit_.key, "mean": _mean(values), "days": len(values)})
            result[tactic.key] = _exit_choices(tactic, exits)
        return result

    def seal_rates(self, slot: str) -> dict[str, Any]:
        return {bucket: {"cumulative_rate": sealed / count if count else None, "count": count}
                for bucket, (sealed, count) in sorted(self.seal[slot].items())}

    def sell_scope(self, slot: str) -> dict[str, Any]:
        sums = self.sell[slot] or _sell_scope_sums({"slot": slot, "rows": []})
        # Buckets in one order whatever day came first (the stored digests come back with their keys reordered).
        return _sell_means({name: group if name == "all" else dict(sorted(group.items())) for name, group in sums.items()})


def _exit_choices(tactic: Tactic, exits: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    available = [item for item in exits.values() if item["mean"] is not None]
    return {
        "entry": tactic.entry, "own": exits[tactic.exit], "close": exits["close"],
        "best": max(available, key=lambda item: item["mean"]) if available else None,
        "worst": min(available, key=lambda item: item["mean"]) if available else None,
    }


def _sell_points(history: History, latest: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Exit returns and room for the latest pick day of each slot and for the whole history."""
    day = History(history.tactics)
    for item in latest:
        day.add(day_digest(item, history.tactics))
    return json_safe({slot: {
        "day": day.sell_scope(slot), "cumulative": history.sell_scope(slot),
        "exit_tables": {"day": day.exit_tables(slot), "cumulative": history.exit_tables(slot)},
    } for slot in ("close", "auction")})


def _pair_condition_keys(slot: str) -> tuple[str, ...]:
    own = tuple(condition.key for condition in CONDITIONS if condition.level == "stock" and condition.slot == slot)
    if slot == "close":
        return own
    previous = tuple(
        f"prev.{condition.key}" for condition in CONDITIONS
        if condition.level == "stock" and condition.slot == "close"
    )
    return own + previous


def day_pairs(day: Mapping[str, Any]) -> dict[str, Any]:
    """One settled day's pairs of stock conditions that some stock met together: picks, their mean, the difference."""
    slot = day["slot"]
    keys = _pair_condition_keys(slot)
    outcome_key = "held:close" if slot == "close" else "buy_open:close"
    cells: dict[tuple[tuple[str, str], str], dict[str, list[Decimal | None]]] = {}
    for row in day["rows"]:
        valid = [key for key in keys if _condition_value(key, row, {}) is not None]
        value = _outcome_value(day.get("outcomes", {}).get(row["symbol"], {}), outcome_key)[0]
        for first_index, first in enumerate(valid):
            for second in valid[first_index + 1:]:
                bucket = cells.setdefault(((first, second), str(row.get("board_bucket"))), {"pick": [], "comparison": []})
                target = bucket["pick"] if first in row.get("flags", ()) and second in row.get("flags", ()) else bucket["comparison"]
                target.append(None if value is None else _number(value))
    states: dict[tuple[str, str], dict[str, Any]] = {}
    for (pair, _cell), bucket in cells.items():
        if not bucket["pick"]:
            continue
        state = states.setdefault(pair, {"picks": 0, "pick_values": [], "differences": []})
        state["picks"] += len(bucket["pick"])
        state["pick_values"].extend(bucket["pick"])
        comparison = _mean(bucket["comparison"])
        if comparison is not None:
            state["differences"].extend(value - comparison for value in bucket["pick"] if value is not None)
    return json_safe({"trade_date": _date(day["trade_date"]), "slot": slot, "pairs": {
        "|".join(pair): [state["picks"], _mean(state["pick_values"]), _mean(state["differences"])]
        for pair, state in states.items()}})


def _condition_name(key: str) -> str:
    """A condition's name; one read from the session before (``prev.``) says so."""
    names = {condition.key: condition.name for condition in CONDITIONS}
    return f"昨日{names[key[5:]]}" if key.startswith("prev.") else names[key]


class PairHistory:
    """The mined pairs over the days folded in, oldest first."""

    def __init__(self) -> None:
        self._pairs: dict[str, dict[str, dict[str, Any]]] = {"close": {}, "auction": {}}

    def add(self, digest: Mapping[str, Any]) -> None:
        for key, (picks, pick_mean, difference) in digest["pairs"].items():
            state = self._pairs[digest["slot"]].setdefault(key, {"days": 0, "picks": 0, "pick_means": array("d"), "differences": array("d")})
            state["days"] += 1
            state["picks"] += picks
            if pick_mean is not None:
                state["pick_means"].append(pick_mean)
            if difference is not None:
                state["differences"].append(difference)

    def result(self) -> dict[str, Any]:
        result = {}
        for slot, states in self._pairs.items():
            pairs = []
            for key, state in states.items():
                differences = list(state["differences"])
                t_value = None
                if len(differences) >= 3:
                    deviation = stdev(differences)
                    t_value = None if deviation == 0 else mean(differences) / (deviation / sqrt(len(differences)))
                pairs.append({"conditions": key.split("|"), "names": [_condition_name(item) for item in key.split("|")], **json_safe({
                    "days_with_picks": state["days"], "picks": state["picks"],
                    "mean_daily_picks_mean": _mean(list(state["pick_means"])),
                    "mean_daily_difference": _mean(differences), "t": t_value,
                    "positive_share": sum(value > 0 for value in differences) / len(differences) if differences else None,
                })})
            keys = len(_pair_condition_keys(slot))
            eligible = [pair for pair in pairs if pair["days_with_picks"] >= 20]
            ranked = sorted((pair for pair in eligible if pair["t"] is not None), key=lambda pair: (pair["t"], pair["conditions"]))
            result[slot] = {
                "mined": True, "pairs_examined": keys * (keys - 1) // 2, "pairs_reaching_minimum": len(eligible),
                "highest": list(reversed(ranked[-15:])), "lowest": ranked[:15],
            }
        return result


def _day_item(history: History, latest: Sequence[Mapping[str, Any]], today_rows: Mapping[str, int]) -> dict[str, Any]:
    """The day section: for each slot the newest pick day that has outcomes, beside today's own sample size."""
    sizes = {
        slot: {
            "pick_day": next((_date(day["trade_date"]) for day in latest if day["slot"] == slot), None),
            "rows": sum(len(day["rows"]) for day in latest if day["slot"] == slot),
            "cumulative_rows": history.rows[slot], "today_rows": today_rows.get(slot),
        }
        for slot in ("close", "auction")
    }
    conditions = {
        slot: list(day.get("day", {}).get("flags", ()))
        for slot in ("close", "auction")
        for day in latest if day["slot"] == slot
    }
    rates = {}
    for slot in ("close", "auction"):
        current = next((_seal_counts(day) for day in latest if day["slot"] == slot), {})
        rates[slot] = {
            bucket: {
                "day_rate": current[bucket][0] / current[bucket][1] if bucket in current else None,
                "cumulative_rate": value["cumulative_rate"],
                "day_count": current[bucket][1] if bucket in current else 0, "count": value["count"],
            }
            for bucket, value in history.seal_rates(slot).items()
        }
    condition_names = {condition.key: condition.name for condition in CONDITIONS if condition.level == "day"}
    return {
        "sample_sizes": sizes, "day_conditions": {
            slot: [condition_names[key] for key in keys] for slot, keys in conditions.items()
        }, "sealing_again": rates,
    }


def _tactic_picks(day: Mapping[str, Any], tactic: Tactic) -> list[dict[str, Any]]:
    if day["slot"] != tactic.slot:
        return []
    picks = []
    for row in day["rows"]:
        if evaluate_tactic(tactic, row, day.get("day", {})) is not True:
            continue
        value = _outcome_value(day.get("outcomes", {}).get(row["symbol"], {}), tactic.primary)[0]
        picks.append({"symbol": row["symbol"], "name": row.get("name"), "boards": row.get("boards"), "primary": value})
    return picks


def _tactic_item(latest: Sequence[Mapping[str, Any]], tactic: Tactic, cached: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    day = next((item for item in latest if item["slot"] == tactic.slot), None)
    result = next((item["results"][tactic.key] for item in cached if item["source"] is day and tactic.key in item["results"]), None)
    return {
        "key": tactic.key, "name": tactic.name, "slot": tactic.slot, "side": tactic.side,
        "primary": tactic.primary,
        "picks": [] if day is None else _tactic_picks(day, tactic),
        "pick_mean": None if result is None else result["outcomes"][tactic.primary]["pick_mean"],
        "difference": None if result is None else result["outcomes"][tactic.primary]["difference"],
    }


BOARD_FIELDS = ("symbol", "name", "sector", "boards", "gap", "auction_amount_rank", "risk_rank", "first_seal_time")


def day_board(day: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """A reading's stocks by side, each with the registered combinations of that side that picked it."""
    board: dict[str, list[dict[str, Any]]] = {"focus": [], "hold": [], "avoid": []}
    for row in day["rows"]:
        picked = [tactic for tactic in TACTICS if tactic.combination
                  and tactic.slot == day["slot"] and evaluate_tactic(tactic, row, day.get("day", {})) is True]
        for side, items in board.items():
            keys = [tactic.key for tactic in picked if tactic.side == side]
            if keys:
                items.append({**{field: row.get(field) for field in BOARD_FIELDS}, "tactics": keys,
                              "conditions": [_condition_name(key) for key in row.get("flags", ())]})
    return json_safe(board)


def tactic_day(day: Mapping[str, Any], tactic: Tactic) -> dict[str, Any]:
    """One day of one tactic: its picks with their primary outcome, their mean and the difference to the rest."""
    result = day_result(tactic, day["rows"], day.get("day", {}), day.get("outcomes", {}), (tactic.primary,))
    return json_safe({"trade_date": _date(day["trade_date"]), "picks": _tactic_picks(day, tactic), "counts": result["counts"],
                  **{key: result["primary"][key] for key in ("pick_mean", "comparison_mean", "difference")}})


def tactic_card(tactic: Tactic) -> dict[str, Any]:
    return {"key": tactic.key, "name": tactic.name, "slot": tactic.slot, "side": tactic.side, "source": tactic.source,
            "entry": tactic.entry, "exit": tactic.exit, "primary": tactic.primary,
            "primary_name": _outcome_name(tactic.primary), "expects": tactic.expects,
            "registered_on": tactic.registered_on.isoformat(), "combination": tactic.combination}


def _condition_day_items(latest: Sequence[Mapping[str, Any]], cached: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for condition in CONDITIONS:
        if condition.level != "stock":
            continue
        day = next((item for item in latest if item["slot"] == condition.slot), None)
        if day is None:
            items.append({"key": condition.key, "name": condition.name, "picks": 0, "difference": None, "pick_mean": None, "comparison_mean": None, "expects": condition.claim})
            continue
        tactic = next(item for item in TACTICS if item.key == f"only.{condition.key}")
        result = next(item["results"][tactic.key] for item in cached if item["source"] is day)
        outcome = result["outcomes"][condition.claim_outcome]
        items.append({
            "key": condition.key, "name": condition.name, "difference": outcome["difference"],
            "picks": result["counts"]["picks"], "pick_mean": outcome["pick_mean"], "comparison_mean": outcome["comparison_mean"], "expects": condition.claim,
        })
    return sorted(items, key=lambda item: (item["difference"] is None, -(item["difference"] or 0)))


def _summary_records(records: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: {
            "name": value["name"], "slot": value["slot"], "primary": value["primary"],
            "primary_name": _outcome_name(value["primary"]),
            "expects": value["expects"], "records": value["outcomes"][value["primary"]],
            "label": value["label"], "q": value["q"],
        }
        for key, value in records.items()
    }


def _cases(latest: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    result = {"seal_again": [], "big_loss": []}
    for day in latest:
        for row in day["rows"]:
            outcome = day.get("outcomes", {}).get(row["symbol"], {})
            case = {"symbol": row["symbol"], "name": row.get("name"), "boards": row.get("boards"), "slot": day["slot"],
                    "flags": list(row.get("flags", ())), "conditions": [_condition_name(key) for key in row.get("flags", ())][:5]}
            if outcome.get("seal") is True and len(result["seal_again"]) < 10:
                result["seal_again"].append(case)
            if outcome.get("big_loss") is True and len(result["big_loss"]) < 10:
                result["big_loss"].append(case)
    return result


def _fmt_pct(value: Any) -> str:
    return "无数据" if value is None else f"{float(value) * 100:+.2f}%"


def _fmt_count(value: Any) -> str:
    return "无数据" if value is None else str(int(value))


def _fmt_number(value: Any) -> str:
    return "无数据" if value is None else f"{float(value):+.2f}"


_SLOT_NAMES = {"close": "收盘", "auction": "竞价"}
_SIDE_NAMES = {"focus": "关注", "avoid": "回避", "hold": "持有参考"}
_BUCKET_NAMES = {"1": "一板", "2": "二板", "3+": "三板以上"}
_ENTRY_NAMES = {"held": "已持有", "buy_open": "开盘买入"}
_LABEL_NAMES = {"collecting": "样本不足", "supports": "支持", "against": "相反", "undecided": "未定"}


def _bucket_name(value: Any) -> str:
    return _BUCKET_NAMES.get(str(value), str(value))


def _outcome_name(outcome: str) -> str:
    if outcome == "seal":
        return "再封板率"
    if outcome == "big_loss":
        return "大面率"
    if outcome == "open_to_close":
        return "开盘到收盘"
    entry, exit_key = outcome.split(":", 1)
    exit_name = next(exit_.name for exit_ in EXITS if exit_.key == exit_key)
    return f"{_ENTRY_NAMES[entry]}·{exit_name}"


def summary_line_day(item: Mapping[str, Any]) -> list[str]:
    lines = ["一、当日"]
    for slot in ("close", "auction"):
        sample = item["sample_sizes"][slot]
        lines.append(f"{_SLOT_NAMES[slot]}：今日样本 {_fmt_count(sample['today_rows'])} 行；下面是选股日 {sample['pick_day'] or '无数据'} 的"
                     f" {_fmt_count(sample['rows'])} 行在其后的表现（累计 {_fmt_count(sample['cumulative_rows'])} 行）")
        rates = item["sealing_again"].get(slot, {})
        for bucket in sorted(rates, key=lambda value: (str(value) != "1", str(value))):
            value = rates[bucket]
            lines.append(f"{_SLOT_NAMES[slot]} {_bucket_name(bucket)}：{_fmt_count(value['day_count'])}只，再封板率 {_fmt_pct(value['day_rate'])}；累计 {_fmt_pct(value['cumulative_rate'])}")
        conditions = item["day_conditions"].get(slot, [])
        lines.append(f"{_SLOT_NAMES[slot]}日条件：{'、'.join(conditions) if conditions else '无数据'}")
    return lines


def summary_line_tactics(items: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = ["二、战法"]
    empty = []
    for item in items:
        if not item["picks"]:
            empty.append(item["name"])
            continue
        picks = "、".join(f"{pick.get('name') or pick['symbol']}（{_fmt_count(pick.get('boards'))}板）" for pick in item["picks"])
        lines.append(f"{item['name']}（{_SLOT_NAMES[item['slot']]}，{_SIDE_NAMES[item['side']]}）：{picks}；{_outcome_name(item['primary'])} {_fmt_pct(item['pick_mean'])}，差值 {_fmt_pct(item['difference'])}")
    lines.append(f"未选中：{'、'.join(empty) if empty else '无数据'}")
    return lines


def summary_line_cases(cases: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[str]:
    lines = ["三、案例"]
    for key, title in (("seal_again", "再封板"), ("big_loss", "大面")):
        lines.append(f"{title}：")
        lines.extend(f"{case.get('name') or case['symbol']}（{_fmt_count(case.get('boards'))}板）：{'、'.join(case.get('conditions') or ['无数据'])}" for case in cases[key])
        if not cases[key]:
            lines.append("无数据")
    return lines


def summary_line_conditions(items: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = ["四、当日条件"]
    selected = list(items[:5]) + list(items[-5:])
    for item in selected:
        claim = "符合主张" if item["difference"] is not None and item["difference"] * item["expects"] > 0 else "背离主张" if item["difference"] is not None else "无数据"
        lines.append(f"{item['name']}：{_fmt_count(item['picks'])}选，选中均值 {_fmt_pct(item['pick_mean'])}，其余均值 {_fmt_pct(item['comparison_mean'])}，差值 {_fmt_pct(item['difference'])}，{claim}")
    return lines


def summary_line_records(records: Mapping[str, Any], changes: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = ["五、战绩"]
    combinations = {tactic.key for tactic in TACTICS if tactic.combination}
    for key, value in records.items():
        if key not in combinations:
            continue
        forward = value["outcomes"][value["primary"]]["forward"]
        lines.append(f"{value['name']}：前向{_fmt_count(forward['days_with_picks'])}日，{_fmt_count(forward['picks'])}选，差值 {_fmt_pct(forward['mean_daily_difference'])}，t {_fmt_number(forward['t'])}，{_LABEL_NAMES[value['label']]}，q {_fmt_pct(value['q'])}")
    for key, value in records.items():
        if key not in combinations:
            continue
        replay = value["outcomes"][value["primary"]]["replay"]
        lines.append(f"{value['name']}（样本内）：{_fmt_count(replay['days_with_picks'])}日，{_fmt_count(replay['picks'])}选，差值 {_fmt_pct(replay['mean_daily_difference'])}，t {_fmt_number(replay['t'])}")
    tactic_names = {tactic.key: tactic.name for tactic in TACTICS}
    lines.extend(
        f"{tactic_names[change['key']]}：由 {_LABEL_NAMES.get(change['previous'], '无数据')} 变为 {_LABEL_NAMES[change['current']]}"
        for change in changes
    )
    return lines


def _sell_lines(scope: Mapping[str, Any], slot: str, prefix: str) -> list[str]:
    values = []
    for entry, exits in scope["day"]["all"]["returns"].items():
        for exit_key, day_value in exits.items():
            cumulative = scope["cumulative"]["all"]["returns"][entry][exit_key]["mean"]
            values.append((cumulative, entry, exit_key, day_value["mean"], scope["day"]["all"]["room"]["s1_high"]["mean"]))
    lines = []
    for _cumulative, entry, exit_key, day_value, room in sorted(values, key=lambda item: (item[0] is None, -(item[0] or 0))):
        rule_name = next(exit_.name for exit_ in EXITS if exit_.key == exit_key)
        lines.append(f"{prefix}{_ENTRY_NAMES[entry]}·{rule_name}：当日 {_fmt_pct(day_value)}，累计 {_fmt_pct(_cumulative)}，首日高点 {_fmt_pct(room)}")
    return lines


def summary_line_sell_points(points: Mapping[str, Any]) -> list[str]:
    lines = ["六、卖点"]
    for slot in ("close", "auction"):
        lines.append(f"{_SLOT_NAMES[slot]}：")
        lines.extend(_sell_lines(points[slot], slot, ""))
        for bucket, group in points[slot]["cumulative"]["board_bucket"].items():
            choices = [
                (value["mean"], entry, exit_key)
                for entry, exits in group["returns"].items() for exit_key, value in exits.items()
                if value["mean"] is not None
            ]
            best = max(choices) if choices else None
            worst = min(choices) if choices else None
            render = lambda choice: "无数据" if choice is None else f"{_ENTRY_NAMES[choice[1]]}·{next(exit_.name for exit_ in EXITS if exit_.key == choice[2])} {_fmt_pct(choice[0])}"
            lines.append(f"{_SLOT_NAMES[slot]} {_bucket_name(bucket)}：最佳 {render(best)}；最差 {render(worst)}")
        tactic_names = {tactic.key: tactic.name for tactic in TACTICS if tactic.combination}
        for key, table in points[slot]["exit_tables"]["cumulative"].items():
            render = lambda item: "无数据" if item is None else f"{next(exit_.name for exit_ in EXITS if exit_.key == item['key'])} {_fmt_pct(item['mean'])}"
            lines.append(f"{tactic_names[key]}：本战法 {render(table['own'])}；最佳 {render(table['best'])}；最差 {render(table['worst'])}")
    return lines


def summary_line_pairs(pairs: Mapping[str, Any]) -> list[str]:
    lines = ["七、两两组合"]
    for slot in ("close", "auction"):
        lines.append(f"{_SLOT_NAMES[slot]}：")
        selected = pairs[slot]["highest"][:5] + pairs[slot]["lowest"][:5]
        for pair in selected:
            first, second = pair["names"]
            lines.append(f"{first} + {second}：{_fmt_count(pair['days_with_picks'])}日，{_fmt_count(pair['picks'])}选，差值 {_fmt_pct(pair['mean_daily_difference'])}，t {_fmt_number(pair['t'])}")
        lines.append(f"已挖掘，检验{_fmt_count(pairs[slot]['pairs_examined'])}对，达到门槛{_fmt_count(pairs[slot]['pairs_reaching_minimum'])}对")
    return lines


def daily_summary(history: History, pairs: PairHistory, latest: Sequence[Mapping[str, Any]],
                  previous_summary: Mapping[str, Any] | None = None, today_rows: Mapping[str, int] | None = None) -> dict[str, Any]:
    """The bounded JSON summary of an evening.

    ``history`` and ``pairs`` are the folded digests. ``latest`` holds, for each slot, the newest pick day that has
    outcomes, in full: on the evening of T that is the day before, whose picks traded today. ``today_rows`` is the
    size of today's own samples, which have no outcome yet.
    """
    tactics = history.tactics
    cached = _cached_days(latest, tactics)
    records = history.records()
    previous_records = {} if previous_summary is None else previous_summary.get("records", {})
    changes = [] if previous_summary is None else [
        {"key": tactic.key, "previous": previous_records.get(tactic.key, {}).get("label"), "current": records[tactic.key]["label"]}
        for tactic in tactics if previous_records.get(tactic.key, {}).get("label") != records[tactic.key]["label"]
    ]
    tactic_items = [_tactic_item(latest, tactic, cached) for tactic in tactics if tactic.combination]
    day_item = _day_item(history, latest, today_rows or {})
    cases = _cases(latest)
    condition_items = _condition_day_items(latest, cached)
    sell_points = _sell_points(history, latest)
    mined = pairs.result()
    lines = [
        *summary_line_day(day_item), *summary_line_tactics(tactic_items), *summary_line_cases(cases),
        *summary_line_conditions(condition_items), *summary_line_records(records, changes),
        *summary_line_sell_points(sell_points), *summary_line_pairs(mined),
    ]
    return json_safe({
        "day": day_item, "tactics": tactic_items, "cases": cases, "conditions": condition_items,
        "records": _summary_records(records), "switch_records": history.switch_records(), "label_changes": changes,
        "sell_points": sell_points, "pairs": mined, "lines": lines,
    })


__all__ = [
    "History", "PairHistory", "REGISTERED_ON", "STATE_OUTCOMES", "TACTICS", "Tactic", "daily_summary", "day_board",
    "day_digest", "day_pairs", "day_result", "evaluate_tactic", "json_safe", "registry_fingerprint", "tactic_card",
    "tactic_day",
]
