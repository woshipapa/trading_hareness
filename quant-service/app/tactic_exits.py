"""Pure entry and exit evidence for tactic-trial picks."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Mapping, Sequence

from .t1_settlement import (
    ROUND_TRIP_COST,
    PriceWindow,
    PricedBar,
    SettlementBar,
    closes_at_up_limit,
    entry_block_reason,
    opens_at_up_limit,
    price_window,
    walk_exit,
)

MAX_SELL_SESSIONS = 10
ExitDecision = str | tuple[str, Decimal]


@dataclass(frozen=True)
class ExitContext:
    symbol: str
    slot: str
    entry_price: Decimal
    reference_price: Decimal
    prior_closes: tuple[Decimal, ...]


ExitFunction = Callable[[ExitContext], Callable[[int, PricedBar], ExitDecision | None]]


@dataclass(frozen=True)
class Exit:
    key: str
    name: str
    source: str
    rule: ExitFunction


def _close(_context: ExitContext) -> Callable[[int, PricedBar], ExitDecision | None]:
    return lambda _index, _bar: "close"


def _open(_context: ExitContext) -> Callable[[int, PricedBar], ExitDecision | None]:
    return lambda _index, _bar: "open"


def _target(percent: str) -> ExitFunction:
    level = Decimal(percent)

    def build(context: ExitContext) -> Callable[[int, PricedBar], ExitDecision | None]:
        target = context.entry_price * (Decimal(1) + level)

        def rule(_index: int, bar: PricedBar) -> ExitDecision | None:
            if bar.open is not None and bar.open > target:
                return "open"
            if bar.high is not None and bar.high >= target:
                return "target", target
            return "close"

        return rule

    return build


def _stop(context: ExitContext) -> Callable[[int, PricedBar], ExitDecision | None]:
    stop = context.entry_price * Decimal("0.97")

    def rule(_index: int, bar: PricedBar) -> ExitDecision | None:
        if bar.open is not None and bar.open < stop:
            return "stop", stop
        if bar.low is not None and bar.low <= stop:
            return "stop", stop
        return "close"

    return rule


def _bracket(stop_first: bool) -> ExitFunction:
    def build(context: ExitContext) -> Callable[[int, PricedBar], ExitDecision | None]:
        target = context.entry_price * Decimal("1.05")
        stop = context.entry_price * Decimal("0.97")

        def rule(_index: int, bar: PricedBar) -> ExitDecision | None:
            target_hit = (bar.open is not None and bar.open > target) or (bar.high is not None and bar.high >= target)
            stop_hit = (bar.open is not None and bar.open < stop) or (bar.low is not None and bar.low <= stop)
            if target_hit and stop_hit:
                return ("stop", stop) if stop_first else ("target", target)
            if target_hit:
                return "open" if bar.open is not None and bar.open > target else ("target", target)
            if stop_hit:
                return "stop", stop
            return "close"

        return rule

    return build


def _hold_while_sealed(context: ExitContext) -> Callable[[int, PricedBar], ExitDecision | None]:
    def rule(_index: int, bar: PricedBar) -> ExitDecision | None:
        sealed = closes_at_up_limit(context.symbol, bar)
        return None if sealed is None or sealed else "close"
    return rule


def _gap_rule(context: ExitContext) -> Callable[[int, PricedBar], ExitDecision | None]:
    def rule(index: int, bar: PricedBar) -> ExitDecision | None:
        sealed = closes_at_up_limit(context.symbol, bar)
        if sealed is None:
            return None
        if index == 0:
            opened_limit = opens_at_up_limit(context.symbol, bar)
            gap = None if bar.open is None or bar.pre_close is None or bar.pre_close <= 0 else bar.open / bar.pre_close - 1
            if opened_limit is None or gap is None:
                return None
            if opened_limit or gap < Decimal("0.05"):
                return None if sealed else "close"
            return "open"
        return None if sealed else "close"
    return rule


def _must_rise(context: ExitContext) -> Callable[[int, PricedBar], ExitDecision | None]:
    highest = context.entry_price
    def rule(_index: int, bar: PricedBar) -> ExitDecision | None:
        nonlocal highest
        if bar.close is None:
            return None
        if bar.close <= highest:
            return "close"
        highest = bar.close
        return None
    return rule


def _below_ma5(context: ExitContext) -> Callable[[int, PricedBar], ExitDecision | None]:
    closes = list(context.prior_closes)
    pending = False
    def rule(_index: int, bar: PricedBar) -> ExitDecision | None:
        nonlocal pending
        if pending:
            pending = False
            return "open"
        if bar.close is None:
            return None
        if len(closes) >= 4 and bar.close < sum(closes[-4:] + [bar.close], Decimal(0)) / 5:
            pending = True
        closes.append(bar.close)
        return None
    return rule


EXITS = (
    Exit("open", "次日开盘", "baseline", _open),
    Exit("close", "次日收盘", "baseline", _close),
    Exit("tp3", "止盈3%", "practice", _target("0.03")),
    Exit("tp5", "止盈5%", "practice", _target("0.05")),
    Exit("tp8", "止盈8%", "practice", _target("0.08")),
    Exit("sl3", "止损3%", "practice", _stop),
    Exit("tp5_sl3_stop_first", "止盈5止损3止损优先", "practice", _bracket(True)),
    Exit("tp5_sl3_target_first", "止盈5止损3止盈优先", "practice", _bracket(False)),
    Exit("hold_while_sealed", "封板继续持有", "teacher:R51", _hold_while_sealed),
    Exit("gap_rule", "高开规则", "study", _gap_rule),
    Exit("must_rise", "只涨不跌", "teacher:R47", _must_rise),
    Exit("below_ma5", "跌破五日均线", "teacher:R47/R48", _below_ma5),
)


def _capped(rule: Callable[[int, PricedBar], ExitDecision | None]) -> Callable[[int, PricedBar], ExitDecision | None]:
    def capped(index: int, bar: PricedBar) -> ExitDecision | None:
        return "close" if index >= MAX_SELL_SESSIONS - 1 else rule(index, bar)
    return capped


def _result(status: str, basis: str, *, reason: str | None = None, raw: Decimal | None = None,
            net: Decimal | None = None, exit_index: int | None = None, field: str | None = None,
            price: Decimal | None = None) -> dict[str, Any]:
    return {
        "status": status, "raw_return": raw, "net_return": net, "exit_index": exit_index,
        "field": field, "reason": reason, "price": price, "price_basis": basis,
    }


def _return_result(
    walked: Mapping[str, Any], *, base: Decimal, basis: str, cost: Decimal,
    absolute_offset: int,
) -> dict[str, Any]:
    if walked["status"] != "settled":
        return _result("not_resolved", basis, reason=walked["reason"])
    raw = walked["price"] / base - 1
    return _result("settled", basis, reason=walked["reason"], raw=raw, net=raw - cost,
                   exit_index=absolute_offset + walked["exit_index"], field=walked["field"], price=walked["price"])


def _basis_reference(reference_close: Decimal | None, window: PriceWindow) -> Decimal | None:
    if reference_close is None:
        return None
    first = window.bars[0].source
    return reference_close * first.adj_factor if window.basis == "adj_factor_adjusted" else reference_close


def _measure(window: PriceWindow, start: int, end: int, reference: Decimal | None, field: str) -> Decimal | None:
    if reference is None or end > len(window.bars):
        return None
    values = [getattr(window.bars[index], field) for index in range(start, end)]
    if any(value is None for value in values):
        return None
    return values[0] / reference - 1 if field in {"high", "low"} else None


def evaluate_pick(
    symbol: str, slot: str, bars: Sequence[SettlementBar | Mapping[str, Any]], reference_close: Decimal | None,
    prior_closes: Sequence[Decimal] = (),
) -> dict[str, Any]:
    """Evaluate held and buy-open outcomes for one close or auction pick."""
    if not bars:
        return {"entries": {entry: {exit_.key: _result("missing_bar", "unadjusted_missing_adj_factor") for exit_ in EXITS} for entry in ("held", "buy_open")},
                "seal": None, "big_loss": None, "s1_high": None, "s1_low": None, "s3_high": None, "open_to_close": None}
    raw_path = [bar if isinstance(bar, SettlementBar) else SettlementBar.from_row(bar) for bar in bars]
    window = price_window(raw_path)
    entry_index = 0 if slot == "auction" else 1
    held_reference = _basis_reference(reference_close, window) if slot == "auction" else window.bars[0].close
    entry_price = window.bars[entry_index].open if entry_index < len(window.bars) else None
    s1_index = 1
    outcomes: dict[str, dict[str, dict[str, Any]]] = {"held": {}, "buy_open": {}}
    for entry in ("held", "buy_open"):
        base = held_reference if entry == "held" else entry_price
        start = s1_index if entry == "held" else entry_index + 1
        missing_entry = base is None or (entry == "buy_open" and entry_index >= len(raw_path))
        if missing_entry:
            status = "missing_bar"
            for exit_ in EXITS:
                outcomes[entry][exit_.key] = _result(status, window.basis)
            continue
        if entry == "buy_open":
            blocked = entry_block_reason(raw_path[entry_index], 1, symbol)
            if blocked:
                for exit_ in EXITS:
                    outcomes[entry][exit_.key] = _result("no_trade", window.basis, reason=blocked)
                continue
        if start >= len(window.bars):
            for exit_ in EXITS:
                outcomes[entry][exit_.key] = _result("not_resolved", window.basis, reason="not_resolved")
            continue
        context = ExitContext(symbol, slot, base, held_reference or base, tuple(
            _basis_reference(value, window) for value in prior_closes if value is not None
        ))
        path = PriceWindow(window.bars[start:], window.basis)
        for exit_ in EXITS:
            rule = _capped(exit_.rule(context))
            walked = walk_exit(path, symbol=symbol, rule=rule)
            outcomes[entry][exit_.key] = _return_result(
                walked, base=base, basis=window.basis, cost=Decimal(0) if entry == "held" else ROUND_TRIP_COST,
                absolute_offset=start,
            )
    s1 = window.bars[s1_index] if s1_index < len(window.bars) else None
    measure_reference = held_reference if held_reference is not None else entry_price
    seal = None if s1 is None else closes_at_up_limit(symbol, s1)
    s1_high = _measure(window, s1_index, s1_index + 1, measure_reference, "high")
    s1_low = _measure(window, s1_index, s1_index + 1, measure_reference, "low")
    s3_high = None
    if measure_reference is not None and s1_index < len(window.bars):
        highs = [window.bars[index].high for index in range(s1_index, min(s1_index + 3, len(window.bars)))]
        s3_high = None if any(value is None for value in highs) else max(highs) / measure_reference - 1
    open_to_close = None if s1 is None or s1.open is None or s1.close is None or s1.open == 0 else s1.close / s1.open - 1
    held_close = outcomes["held"].get("close")
    big_loss = None if held_close is None or held_close["raw_return"] is None else held_close["raw_return"] <= Decimal("-0.05")
    return {"entries": outcomes, "seal": seal, "big_loss": big_loss, "s1_high": s1_high,
            "s1_low": s1_low, "s3_high": s3_high, "open_to_close": open_to_close}


__all__ = ["EXITS", "MAX_SELL_SESSIONS", "Exit", "ExitContext", "evaluate_pick"]
