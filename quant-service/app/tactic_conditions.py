"""Pure history features and sample builders for tactic-trial evidence."""

from __future__ import annotations

from dataclasses import dataclass
from math import pow
from typing import Any, Callable, Mapping, Sequence

from .limit_up_risk import _seal_minutes, _seal_time

HISTORY_FIELDS = (
    "vr", "turnover_vs_max250", "runup_20", "close_vs_high60",
    "cost_share_below_close", "cost_share_0_30_above",
)
ConditionTest = Callable[[Mapping[str, Any]], bool | None]


@dataclass(frozen=True)
class Condition:
    key: str
    name: str
    slot: str
    level: str
    test: ConditionTest
    source: str
    claim: int
    claim_outcome: int
    history: Mapping[str, str]


def _price(row: Mapping[str, Any], field: str, adjusted: bool) -> float:
    value = float(row[field])
    return value * float(row["adj_factor"]) if adjusted else value


def _amount_cny(row: Mapping[str, Any]) -> float:
    # The window expects amount in thousand CNY and volume in lots; convert amount once to CNY.
    return float(row["amount"]) * 1000.0


def history_features(window: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Compute the close-day history features from oldest-first bars."""
    current = window[-1]
    previous = list(window[:-1])
    adjusted = all(row.get("adj_factor") is not None and float(row["adj_factor"]) > 0 for row in window)
    features: dict[str, float | None] = {
        "history_basis": "adjusted" if adjusted else "unadjusted_missing_adj_factor",
    }

    recent = previous[-20:]
    features["vr"] = (
        _amount_cny(current) / (sum(_amount_cny(row) for row in recent) / len(recent))
        if len(recent) >= 10 else None
    )

    if len(previous) < 60:
        features["turnover_vs_max250"] = None
    else:
        max_turnover = max(_amount_cny(row) for row in previous[-250:])
        features["turnover_vs_max250"] = _amount_cny(current) / max_turnover

    if len(window) < 21:
        features["runup_20"] = None
    else:
        features["runup_20"] = _price(current, "close", adjusted) / _price(window[-21], "close", adjusted) - 1

    if len(previous) < 60:
        features["close_vs_high60"] = None
    else:
        highest = max(_price(row, "high", adjusted) for row in previous[-60:])
        features["close_vs_high60"] = _price(current, "close", adjusted) / highest - 1

    valid = []
    for age, row in enumerate(reversed(previous[-120:]), 1):
        volume = float(row["volume"])
        if volume <= 0:
            continue
        weight = volume * pow(0.5, age / 20.0)
        average_price = _amount_cny(row) / (volume * 100.0)
        if adjusted:
            average_price *= float(row["adj_factor"])
        valid.append((weight, average_price))
    if len(valid) < 20:
        features["cost_share_below_close"] = None
        features["cost_share_0_30_above"] = None
    else:
        total = sum(weight for weight, _price in valid)
        close = _price(current, "close", adjusted)
        features["cost_share_below_close"] = sum(weight for weight, price in valid if price < close) / total
        features["cost_share_0_30_above"] = sum(
            weight for weight, price in valid if close <= price < close * 1.30
        ) / total
    return features


def _board_bucket(boards: float) -> str:
    return "1" if boards == 1 else "2" if boards == 2 else "3+"


def build_trial_row(
    decoded: Mapping[str, Any], features: Mapping[str, float | None], risk_row: Mapping[str, Any],
    decoded_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build one close-slot row from decoded evidence and the stored risk row."""
    bar = decoded["bar"]
    sector = decoded["sector"]
    return {
        "symbol": decoded["symbol"], "name": decoded["name"], "sector": sector,
        "boards": decoded["boards"], "board_bucket": _board_bucket(float(decoded["boards"])),
        "first_seal_time": _seal_time(decoded["first_seal_time"]).strftime("%H:%M:%S"),
        "seal_money": decoded["seal_money"], "seal_max": decoded["seal_max"],
        "main_net": decoded["main_net"], "turnover": decoded["turnover"], "float_cap": decoded["float_cap"],
        "reopened": decoded["reopened"], "sealed_from_open": _seal_minutes(decoded["first_seal_time"]) == 0,
        "one_word": float(bar["high"]) == float(bar["low"]),
        "vr": features["vr"],
        "gap": float(bar["open"]) / float(bar["pre_close"]) - 1,
        "risk_rank": risk_row.get("risk_rank"),
        "theme_count": sum(row["sector"] == sector for row in decoded_rows),
        **{field: features[field] for field in HISTORY_FIELDS},
    }


def build_day_context(
    rows: Sequence[Mapping[str, Any]], market_turnover: Any, market_turnover_median60: Any,
    prev_top_board_sealed_again: Any,
) -> dict[str, Any]:
    """Build the close-slot day context from sample rows and aggregate inputs."""
    return {
        "limit_up_count": len(rows),
        "boards_3plus_count": sum(float(row["boards"]) >= 3 for row in rows),
        "market_turnover": market_turnover,
        "market_turnover_median60": market_turnover_median60,
        "prev_top_board_sealed_again": prev_top_board_sealed_again,
    }


def _value(row: Mapping[str, Any], key: str) -> Any:
    return row.get(key)


def _compare(row: Mapping[str, Any], key: str, predicate: Callable[[float], bool]) -> bool | None:
    value = _value(row, key)
    return None if value is None else predicate(float(value))


def _seal_seconds(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        hours, minutes, seconds = (float(part) for part in value.split(":"))
        return hours * 3600 + minutes * 60 + seconds
    return _seal_time(value).hour * 3600 + _seal_time(value).minute * 60 + _seal_time(value).second


def _seal_before(row: Mapping[str, Any], hour: int, minute: int = 0) -> bool | None:
    seal = _seal_seconds(_value(row, "first_seal_time"))
    return None if seal is None else seal < hour * 3600 + minute * 60


def _seal_at_or_after(row: Mapping[str, Any], hour: int, minute: int = 0) -> bool | None:
    seal = _seal_seconds(_value(row, "first_seal_time"))
    return None if seal is None else seal >= hour * 3600 + minute * 60


def _history(status: str, text: str) -> dict[str, str]:
    return {"status": status, "text": text}


def _condition(
    key: str, name: str, slot: str, level: str, test: ConditionTest, source: str, claim: int, status: str, text: str,
) -> Condition:
    return Condition(key, name, slot, level, test, source, claim, claim, _history(status, text))


def _bool_field(key: str, expected: bool) -> ConditionTest:
    def test(row: Mapping[str, Any]) -> bool | None:
        value = _value(row, key)
        return None if value is None else bool(value) is expected
    return test


def _gap_and(row: Mapping[str, Any], predicate: Callable[[float], bool]) -> bool | None:
    return _compare(row, "gap", predicate)


def _prev_turnover(row: Mapping[str, Any]) -> float | None:
    previous = row.get("prev")
    return None if previous is None else previous.get("turnover")


def _close_conditions() -> tuple[Condition, ...]:
    return (
        _condition("theme_big", "大题材", "close", "stock", lambda r: _compare(r, "theme_count", lambda v: v >= 10), "teacher:R06", 1, "holds", "题材数量支持"),
        _condition("theme_7plus", "七只以上题材", "close", "stock", lambda r: _compare(r, "theme_count", lambda v: v >= 7), "study", 1, "holds", "题材数量支持"),
        _condition("theme_lone", "小题材", "close", "stock", lambda r: _compare(r, "theme_count", lambda v: v <= 3), "teacher:R06/R07", 1, "holds", "收益比较支持"),
        _condition("theme_leader_earliest", "题材最早封板", "close", "stock", lambda r: r.get("_theme_leader_earliest"), "teacher:R01/R21", 1, "holds", "持有者比较支持"),
        _condition("theme_leader_top_board", "题材最高板", "close", "stock", lambda r: r.get("_theme_leader_top_board"), "teacher:R01", 1, "holds", "部分比较支持"),
        _condition("market_top_board", "市场最高板", "close", "stock", lambda r: r.get("_market_top_board"), "teacher:R11", 1, "holds", "持有者比较支持"),
        _condition("board_2_3", "二至三板", "close", "stock", lambda r: _compare(r, "boards", lambda v: 2 <= v <= 3), "teacher:R11", 1, "mixed", "板数比较混合"),
        _condition("board_4plus", "四板以上", "close", "stock", lambda r: _compare(r, "boards", lambda v: v >= 4), "teacher:R11", 1, "mixed", "板数比较混合"),
        _condition("board_6plus", "六板以上", "close", "stock", lambda r: _compare(r, "boards", lambda v: v >= 6), "teacher:R11", 1, "mixed", "板数比较混合"),
        _condition("seal_small", "封单小", "close", "stock", lambda r: _compare(r, "seal_money", lambda v: v < 20_000_000), "teacher:R20", 1, "holds", "封单比较支持"),
        _condition("seal_large", "封单大", "close", "stock", lambda r: _compare(r, "seal_money", lambda v: v >= 300_000_000), "teacher:R20", 1, "mixed", "封单比较混合"),
        _condition("volume_heavy", "放量", "close", "stock", lambda r: _compare(r, "vr", lambda v: v >= 3), "teacher:R15/R27", 1, "holds", "成交量比较支持"),
        _condition("volume_shrunk", "缩量", "close", "stock", lambda r: _compare(r, "vr", lambda v: v < 1), "teacher:R19", 1, "mixed", "成交量比较混合"),
        _condition("amount_record_250", "成交额创纪录", "close", "stock", lambda r: _compare(r, "turnover_vs_max250", lambda v: v >= 1), "teacher:R34", -1, "against", "次日收益相反"),
        _condition("seal_late", "晚封板", "close", "stock", lambda r: _seal_at_or_after(r, 14, 30), "teacher:R21", 1, "holds", "封板时间比较支持"),
        _condition("seal_early", "早封板", "close", "stock", lambda r: (
            None if _seal_before(r, 10) is None or r.get("sealed_from_open") is None
            else _seal_before(r, 10) and not r["sealed_from_open"]
        ), "teacher:R21", 1, "untested", "尚无样本结论"),
        _condition("sealed_from_open", "竞价封板", "close", "stock", _bool_field("sealed_from_open", True), "teacher:R17", 1, "holds", "竞价封板比较支持"),
        _condition("reopened", "开板回封", "close", "stock", _bool_field("reopened", True), "teacher:R18/R22", -1, "against", "比较结果相反"),
        _condition("runup_40", "二十日涨幅高", "close", "stock", lambda r: _compare(r, "runup_20", lambda v: v >= 0.40), "teacher:R49", 1, "holds", "大亏损比较支持"),
        _condition("turnover_10bn", "成交额百亿", "close", "stock", lambda r: _compare(r, "turnover", lambda v: v >= 10_000_000_000), "teacher:R36", 1, "mixed", "成交额比较混合"),
        _condition("above_high60", "突破六十日高", "close", "stock", lambda r: _compare(r, "close_vs_high60", lambda v: v > 0), "teacher:R31", 1, "mixed", "比较结果混合"),
        _condition("main_net_out", "主力净流出", "close", "stock", lambda r: _compare(r, "main_net", lambda v: v < 0), "study", 1, "holds", "比较结果支持"),
        _condition("risk_top_fifth", "风险前五分之一", "close", "stock", lambda r: _compare(r, "risk_rank", lambda v: v >= 80), "study", 1, "holds", "风险分层支持"),
        _condition("catch_up_first_board", "题材补涨首板", "close", "stock", lambda r: r.get("_catch_up_first_board"), "practice", 1, "mixed", "比较结果混合"),
        _condition("cost_above_band", "成本带上方", "close", "stock", lambda r: _compare(r, "cost_share_below_close", lambda v: v >= 0.95), "practice (筹码)", 1, "mixed", "成本代理比较混合"),
        _condition("cost_overhead_heavy", "成本上方密集", "close", "stock", lambda r: _compare(r, "cost_share_0_30_above", lambda v: v >= 0.30), "practice (筹码)", 1, "mixed", "成本代理比较混合"),
    )


def _day_conditions() -> tuple[Condition, ...]:
    return (
        _condition("env_ladder_ok", "梯队环境", "close", "day", lambda r: (
            None if r.get("boards_3plus_count") is None or r.get("limit_up_count") is None
            else r["boards_3plus_count"] > 5 and r["limit_up_count"] > 20
        ), "teacher:R13", -1, "against", "环境条件比较相反"),
        _condition("env_turnover_2p1", "市场成交额高", "close", "day", lambda r: _compare(r, "market_turnover", lambda v: v >= 2.1e12), "teacher:R38", -1, "against", "环境条件比较相反"),
        _condition("env_turnover_above_median60", "成交额高于六十日中位数", "close", "day", lambda r: (
            None if r.get("market_turnover") is None or r.get("market_turnover_median60") is None
            else r["market_turnover"] > r["market_turnover_median60"]
        ), "study", 1, "untested", "尚无样本结论"),
        _condition("env_top_board_failed", "最高板未回封", "close", "day", lambda r: _bool_field("prev_top_board_sealed_again", False)(r), "study", 1, "mixed", "环境比较混合"),
    )


def _auction_conditions() -> tuple[Condition, ...]:
    return (
        _condition("opens_at_limit", "竞价涨停开", "auction", "stock", _bool_field("opens_at_limit", True), "teacher:R17", 1, "holds", "竞价比较支持"),
        _condition("gap_down_2", "低开二点", "auction", "stock", lambda r: _gap_and(r, lambda v: v < -0.02), "study", 1, "holds", "低开比较支持"),
        _condition("gap_up_5_not_sealed", "高开五点未封", "auction", "stock", lambda r: (
            None if _value(r, "opens_at_limit") is None or _value(r, "gap") is None
            else r["gap"] >= 0.05 and not r["opens_at_limit"]
        ), "teacher:R23", 1, "holds", "高开比较支持"),
        _condition("gap_3_to_8", "高开三至八点", "auction", "stock", lambda r: _gap_and(r, lambda v: 0.03 <= v < 0.08), "teacher:R23", -1, "against", "买入收益相反"),
        _condition("gap_0_to_5", "平开至高开五点", "auction", "stock", lambda r: _gap_and(r, lambda v: 0 <= v < 0.05), "study", 1, "untested", "尚无样本结论"),
        _condition("auction_amount_top5", "竞价金额前五", "auction", "stock", lambda r: _compare(r, "auction_amount_rank", lambda v: v <= 5), "study", 1, "mixed", "竞价比较混合"),
        _condition("auction_burst", "竞价放大", "auction", "stock", lambda r: (
            None if _value(r, "amount") is None or _prev_turnover(r) is None
            else r["amount"] >= 0.217 * _prev_turnover(r)
        ), "practice", 1, "mixed", "竞价比较混合"),
        _condition("board_gap_below_typical", "板数对应低开", "auction", "stock", lambda r: (
            None if any(_value(r, key) is None for key in ("boards", "gap", "opens_at_limit"))
            else r["boards"] >= 2 and not r["opens_at_limit"] and r["gap"] < (0.0543 if r["boards"] == 2 else 0.0860)
        ), "study", 1, "mixed", "竞价比较混合"),
    )


def _role_condition(key: str, name: str, test_key: str, source: str, claim: int, status: str, text: str) -> Condition:
    return _condition(key, name, "close", "stock", lambda row: row.get(test_key), source, claim, status, text)


def _ladder_conditions() -> tuple[Condition, ...]:
    return (
        _role_condition("tier_first", "同梯队最早", "_tier_first", "teacher:R12", 1, "holds", "梯队比较支持"),
        _role_condition("front_row", "前排卡位", "_front_row", "teacher:R03", 1, "holds", "位置比较支持"),
        _role_condition("kawei_leader", "卡位龙头", "_kawei_leader", "teacher:R03", 1, "holds", "位置比较支持"),
        _role_condition("leader_switch", "龙头切换", "_leader_switch", "practice", 1, "mixed", "比较结果混合"),
        _role_condition("assist", "助攻", "_assist", "teacher:R07/R21", 1, "mixed", "比较结果混合"),
        _role_condition("back_row", "后排", "_back_row", "teacher:R07", -1, "holds", "弱势比较支持"),
        _role_condition("sole_high_board", "独苗高标", "_sole_high_board", "practice", -1, "against", "没有差异"),
        _role_condition("ladder_full", "梯队完整", "_ladder_full", "practice", 1, "mixed", "比较结果混合"),
        _role_condition("weak_to_strong", "弱转强", "_weak_to_strong", "practice", -1, "against", "比较结果相反"),
        _role_condition("divergence_to_consensus", "分歧转一致", "_divergence_to_consensus", "teacher:R17/R18", 1, "holds", "持有者比较支持"),
        _role_condition("consensus_to_divergence", "一致转分歧", "_consensus_to_divergence", "teacher:R17/R18", -1, "holds", "风险比较支持"),
        _role_condition("rebound_board", "反包", "_rebound_board", "teacher:R14", -1, "holds", "大亏损比较支持"),
    )


CONDITIONS = _close_conditions() + _day_conditions() + _auction_conditions() + _ladder_conditions()


def build_auction_row(
    row: Mapping[str, Any], pool: Sequence[Mapping[str, Any]], previous_close: Mapping[str, Any] | None,
) -> dict[str, Any]:
    amount = row.get("amount")
    ranked = sorted(
        (item for item in pool if item.get("amount") is not None),
        key=lambda item: (-float(item["amount"]), str(item["symbol"])),
    )
    rank = next((index for index, item in enumerate(ranked, 1) if item["symbol"] == row["symbol"]), None)
    boards = row.get("boards")
    return {
        "symbol": row["symbol"], "name": row.get("name"), "open": row.get("open"), "gap": row.get("gap"),
        "opens_at_limit": row.get("opens_at_limit"), "gap_bucket": row.get("bucket"),
        "risk_rank": row.get("risk_rank"), "boards": boards,
        "board_bucket": _board_bucket(float(boards)) if boards is not None else None,
        "sealed_from_open": row.get("sealed_from_open"), "amount": amount, "volume": row.get("volume"),
        "unmatched": row.get("unmatched"), "auction_amount_rank": rank if amount is not None else None,
        "prev": previous_close,
    }


def _derived_close(row: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dict[str, bool | None]:
    sector = row.get("sector")
    theme = [item for item in rows if item.get("sector") == sector]
    if sector is None or any(item.get("boards") is None or item.get("first_seal_time") is None for item in theme):
        leader_earliest = leader_top = catch_up = None
    elif len(theme) < 2:
        leader_earliest = leader_top = catch_up = None
    else:
        earliest = min(theme, key=lambda item: (_seal_seconds(item.get("first_seal_time")), -float(item["boards"]), item["symbol"]))
        top = min(theme, key=lambda item: (-float(item["boards"]), _seal_seconds(item.get("first_seal_time")), item["symbol"]))
        leader_earliest = row["symbol"] == earliest["symbol"]
        leader_top = row["symbol"] == top["symbol"]
        catch_up = float(row["boards"]) == 1 and float(top["boards"]) >= 3
    market_top = None if not rows else (
        None if any(item.get("boards") is None for item in rows) else float(row["boards"]) == max(float(item["boards"]) for item in rows) and float(row["boards"]) >= 3
    )
    return {
        "_theme_leader_earliest": leader_earliest, "_theme_leader_top_board": leader_top,
        "_market_top_board": market_top, "_catch_up_first_board": catch_up,
    }


def _condition_value(
    condition: Condition, row: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], role_values: Mapping[str, Any] | None = None,
) -> bool | None:
    return condition.test({**row, **_derived_close(row, rows), **(role_values or {})})


def _complete_rows(reading: Mapping[str, Any] | None) -> list[Mapping[str, Any]] | None:
    return None if reading is None or not reading["complete"] else list(reading["rows"])


def _row_for(rows: Sequence[Mapping[str, Any]], symbol: str) -> Mapping[str, Any] | None:
    return next((row for row in rows if row["symbol"] == symbol), None)


def _theme_leader(rows: Sequence[Mapping[str, Any]], sector: Any) -> Mapping[str, Any] | None:
    theme = [row for row in rows if row.get("sector") == sector]
    if len(theme) < 2:
        return None
    return min(theme, key=lambda row: (-float(row["boards"]), _seal_seconds(row["first_seal_time"]), row["symbol"]))


def _role_values(row: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], previous: Sequence[Mapping[str, Any] | None]) -> dict[str, bool | None]:
    sector = row.get("sector")
    theme = [item for item in rows if item.get("sector") == sector]
    tier = [item for item in rows if item.get("boards") == row.get("boards")]
    leader = _theme_leader(rows, sector)
    seal = _seal_seconds(row.get("first_seal_time"))
    values: dict[str, bool | None] = {}
    values["_tier_first"] = (
        None if row.get("boards") is None or seal is None else
        len(tier) >= 3 and float(row["boards"]) >= 2 and row["symbol"] == min(tier, key=lambda item: (_seal_seconds(item["first_seal_time"]), item["symbol"]))["symbol"]
    )
    if leader is None or seal is None:
        values["_front_row"] = values["_assist"] = values["_back_row"] = None
    else:
        leader_seal = _seal_seconds(leader["first_seal_time"])
        values["_front_row"] = float(row["boards"]) < float(leader["boards"]) and seal < leader_seal
        values["_back_row"] = float(row["boards"]) < float(leader["boards"]) and seal > leader_seal
        values["_assist"] = float(row["boards"]) == 1 and float(leader["boards"]) >= 2 and 0 <= seal - leader_seal <= 1800
    values["_sole_high_board"] = None if row.get("boards") is None else float(row["boards"]) >= 3 and len(tier) == 1
    buckets = {1 if float(item["boards"]) == 1 else 2 if float(item["boards"]) == 2 else 3 for item in theme}
    values["_ladder_full"] = None if not theme else buckets == {1, 2, 3}
    prior_rows = _complete_rows(previous[0]) if previous else None
    prior = None if prior_rows is None else _row_for(prior_rows, row["symbol"])
    prior_leader = None if prior_rows is None else _theme_leader(prior_rows, sector)
    if prior_rows is None or prior_leader is None:
        values["_kawei_leader"] = values["_leader_switch"] = None
    else:
        today_leader_seal = None if leader is None else _seal_seconds(leader["first_seal_time"])
        prior_leader_today = _row_for(rows, prior_leader["symbol"])
        values["_kawei_leader"] = (
            None if today_leader_seal is None or prior_leader_today is None else
            row["symbol"] != prior_leader["symbol"] and seal < _seal_seconds(prior_leader_today["first_seal_time"])
        )
        values["_leader_switch"] = (
            row["symbol"] == (leader or {}).get("symbol") and prior_leader_today is None and len(theme) >= 2
        )
    if prior is None:
        values["_weak_to_strong"] = values["_divergence_to_consensus"] = values["_consensus_to_divergence"] = None
    else:
        weak = (
            _seal_at_or_after(prior, 13) is True or bool(prior.get("reopened")) or
            (prior.get("seal_money") is not None and float(prior["seal_money"]) < 20_000_000)
        )
        values["_weak_to_strong"] = None if seal is None or row.get("boards") is None else float(row["boards"]) >= 2 and weak and seal <= 9 * 3600 + 45 * 60
        values["_divergence_to_consensus"] = None if row.get("one_word") is None else (bool(prior.get("reopened")) or (prior.get("vr") is not None and float(prior["vr"]) >= 2)) and bool(row["one_word"])
        values["_consensus_to_divergence"] = None if row.get("one_word") is None or prior.get("one_word") is None else bool(prior["one_word"]) and not bool(row["one_word"])
    values["_rebound_board"] = _rebound_value(row, previous)
    return values


def _rebound_value(row: Mapping[str, Any], previous: Sequence[Mapping[str, Any] | None]) -> bool | None:
    if row.get("boards") is None:
        return None
    candidates: list[bool] = []
    unknown = False
    for lag in (2, 3):
        index = lag - 1
        history = _complete_rows(previous[index]) if len(previous) > index else None
        if history is None:
            unknown = True
            continue
        before = _row_for(history, row["symbol"])
        if before is None or float(before["boards"]) < 2:
            candidates.append(False)
            continue
        absent = all(_row_for(_complete_rows(previous[offset]) or [], row["symbol"]) is None for offset in range(index))
        candidates.append(float(row["boards"]) == 1 and absent)
    return True if any(candidates) else None if unknown else False


def evaluate_ladder_roles(
    rows: Sequence[Mapping[str, Any]], previous: Sequence[Mapping[str, Any] | None],
) -> list[dict[str, Any]]:
    roles = {row["symbol"]: _role_values(row, rows, previous) for row in rows}
    result = []
    role_keys = tuple(condition.key for condition in _ladder_conditions())
    for row in rows:
        values = roles[row["symbol"]]
        result.append({
            **row,
            "flags": [key for key in role_keys if values[f"_{key}"] is True],
            "unknown": [key for key in role_keys if values[f"_{key}"] is None],
        })
    return result


def evaluate_conditions(
    rows: Sequence[Mapping[str, Any]], day_context: Mapping[str, Any], slot: str,
    previous: Sequence[Mapping[str, Any] | None] = (),
) -> dict[str, Any]:
    stock_conditions = tuple(condition for condition in CONDITIONS if condition.level == "stock" and condition.slot == slot)
    day_conditions = tuple(condition for condition in CONDITIONS if condition.level == "day" and condition.slot == slot)
    evaluated_rows = []
    for source in rows:
        row = dict(source)
        role_values = _role_values(row, rows, previous) if slot == "close" else {}
        flags: list[str] = []
        unknown: list[str] = []
        for condition in stock_conditions:
            value = _condition_value(condition, row, rows, role_values)
            if value is True:
                flags.append(condition.key)
            elif value is None:
                unknown.append(condition.key)
        if slot == "auction":
            previous = row.get("prev")
            for condition in _close_conditions():
                key = f"prev.{condition.key}"
                if previous is None or not previous.get("complete", True):
                    unknown.append(key)
                elif condition.key in previous.get("flags", []):
                    flags.append(key)
                elif condition.key in previous.get("unknown", []):
                    unknown.append(key)
        row["flags"], row["unknown"] = flags, unknown
        evaluated_rows.append(row)
    day_flags: list[str] = []
    day_unknown: list[str] = []
    for condition in day_conditions:
        value = condition.test(day_context)
        if value is True:
            day_flags.append(condition.key)
        elif value is None:
            day_unknown.append(condition.key)
    return {"rows": evaluated_rows, "day": {"flags": day_flags, "unknown": day_unknown}}


__all__ = [
    "CONDITIONS", "Condition", "HISTORY_FIELDS", "build_auction_row", "build_day_context", "build_trial_row",
    "evaluate_conditions", "history_features",
]
