"""Pure tactic definitions and daily evidence comparisons."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Mapping, Sequence

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
    valid = [Decimal(int(value)) if isinstance(value, bool) else Decimal(str(value)) for value in values if value is not None]
    return None if not valid else sum(valid, Decimal(0)) / len(valid)


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
            differences.append(Decimal(str(value)) - comparison_mean)
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
    outcomes: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Score one tactic's picks against non-picked rows in the same cells."""
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
    scored = {key: _summary(picks, comparisons, outcomes, key) for key in _outcome_keys()}
    return {"primary": scored[tactic.primary], "outcomes": scored, "counts": counts}


__all__ = ["REGISTERED_ON", "STATE_OUTCOMES", "TACTICS", "Tactic", "day_result", "evaluate_tactic"]
