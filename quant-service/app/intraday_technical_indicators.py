"""Advance MACD/KDJ from a daily seed to a live price.

The seed is the previous session's converged EMA and K/D state.  It used to
be read from Tushare's ``stk_factor_pro``, on the premise that local history
was too short to converge a MACD(12,26,9), which needs roughly 60-100
sessions.  That premise no longer holds: on 2026-10-09 the owner's
``canonical_bars_daily`` held about 680 sessions per symbol (3.85 million rows
over 5,676 symbols), and Tushare was retired the day before (decision 0005),
its newest ``stk_factor_pro`` row being from 2026-09-17.  So the seed is now
computed from the symbol's own unadjusted daily bars - the last
``SEED_HISTORY_SESSIONS`` of them, which leaves EMA26's start-up error at
(25/27)**250, about 1e-8 of the first close - and the live value is that
seed advanced one step with the running price.

The recursion is the vendor's, checked twice.  Against its consecutive rows
for 600176.SH (2026-09-10..2026-09-16): the histogram is 2*(DIF-DEA), DEA is
a 9-period EMA of DIF, EXPMA12 advances with alpha 2/13, D is a 3-period
smoothing of K, and RSV's window counts the current session's own high/low.
And computed from 250 unadjusted bars to 2026-09-15, the seed equals the
vendor's published row for that day: EXPMA12 43.69487, K 72.77585 and
D 62.04631 to every printed digit, DIF and DEA within its three-decimal
rounding (tests/fixtures/600176_daily_bfq_to_20260915.json).

Seed and live price must share one adjustment basis.  Live quotes and the
canonical bars are both unadjusted, so ``bfq`` is the only basis a computed
seed serves; pairing a front-adjusted seed with an unadjusted price would
corrupt every value from the next ex-dividend date onward.

Research-only: these are observation features, not an order instruction.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

MACD_FAST_PERIODS = 12
MACD_SLOW_PERIODS = 26
MACD_SIGNAL_PERIODS = 9
KDJ_WINDOW_SESSIONS = 9
KDJ_SMOOTHING_PERIODS = 3

#: The adjustment bases a seed row may carry (``stk_factor_pro`` published all three).
ADJUSTMENT_BASES = ("bfq", "qfq", "hfq")
#: Sessions read to compute a seed, and the fewest that count as converged.
SEED_HISTORY_SESSIONS = 250
MIN_SEED_SESSIONS = 120


def _alpha(periods: int) -> float:
    return 2.0 / (periods + 1)


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _require_basis(basis: str) -> str:
    if basis not in ADJUSTMENT_BASES:
        raise ValueError(f"adjustment basis must be one of {', '.join(ADJUSTMENT_BASES)}: {basis}")
    return basis


def _factor(row: Mapping[str, Any], name: str, basis: str) -> float | None:
    return _number(row.get(f"{name}_{basis}"))


def macd_seed(row: Mapping[str, Any], *, basis: str = "bfq") -> dict[str, Any] | None:
    """Recover the EMA state MACD needs from one published factor row."""
    _require_basis(basis)
    ema_fast = _factor(row, "expma_12", basis)
    dif = _factor(row, "macd_dif", basis)
    dea = _factor(row, "macd_dea", basis)
    if ema_fast is None or dif is None or dea is None:
        return None
    return {
        "basis": basis,
        "trade_date": str(row.get("trade_date") or "") or None,
        "ema_fast": ema_fast,
        "ema_slow": ema_fast - dif,
        "dea": dea,
    }


def kdj_seed(row: Mapping[str, Any], *, basis: str = "bfq") -> dict[str, Any] | None:
    """Carry the previous session's K and D; J is derived, never carried."""
    _require_basis(basis)
    k = _factor(row, "kdj_k", basis)
    d = _factor(row, "kdj_d", basis)
    if k is None or d is None:
        return None
    return {
        "basis": basis,
        "trade_date": str(row.get("trade_date") or "") or None,
        "k": k,
        "d": d,
    }


def advance_macd(seed: Mapping[str, Any] | None, price: Any) -> dict[str, Any] | None:
    """Advance one session.  Repeat calls restate the same session, not the next."""
    close = _number(price)
    if not seed or close is None:
        return None
    ema_fast = _number(seed.get("ema_fast"))
    ema_slow = _number(seed.get("ema_slow"))
    dea_prev = _number(seed.get("dea"))
    if ema_fast is None or ema_slow is None or dea_prev is None:
        return None
    ema_fast = ema_fast + _alpha(MACD_FAST_PERIODS) * (close - ema_fast)
    ema_slow = ema_slow + _alpha(MACD_SLOW_PERIODS) * (close - ema_slow)
    dif = ema_fast - ema_slow
    dea = dea_prev + _alpha(MACD_SIGNAL_PERIODS) * (dif - dea_prev)
    return {
        "dif": round(dif, 4),
        "dea": round(dea, 4),
        "macd": round(2 * (dif - dea), 4),
        "ema_fast": round(ema_fast, 4),
        "ema_slow": round(ema_slow, 4),
        "seed_trade_date": seed.get("trade_date"),
        "basis": seed.get("basis"),
    }


def kdj_window_bounds(
    prior_sessions: Iterable[Mapping[str, Any]], *, session_high: Any, session_low: Any,
) -> tuple[float, float] | None:
    """Merge the running session into the prior eight completed sessions.

    The RSV window counts the current session, so nine completed daily bars
    plus a live tick spans ten sessions - one session too wide, which drags RSV
    down in an uptrend exactly when the signal matters.  Taking the last eight
    and folding today's running high/low in keeps the window at nine.
    """
    highs: list[float] = []
    lows: list[float] = []
    for bar in list(prior_sessions)[-(KDJ_WINDOW_SESSIONS - 1):]:
        high, low = _number(bar.get("high")), _number(bar.get("low"))
        if high is None or low is None:
            return None
        highs.append(high)
        lows.append(low)
    live_high, live_low = _number(session_high), _number(session_low)
    if live_high is None or live_low is None:
        return None
    if len(highs) < KDJ_WINDOW_SESSIONS - 1:
        return None
    return max(highs + [live_high]), min(lows + [live_low])


def advance_kdj(
    seed: Mapping[str, Any] | None, *, price: Any, window_high: Any, window_low: Any,
) -> dict[str, Any] | None:
    """Advance one session from the carried K/D and the nine-session range."""
    close = _number(price)
    high = _number(window_high)
    low = _number(window_low)
    if not seed or close is None or high is None or low is None:
        return None
    k_prev, d_prev = _number(seed.get("k")), _number(seed.get("d"))
    if k_prev is None or d_prev is None:
        return None
    span = high - low
    if span <= 0:
        # Nine sessions at one price: RSV is 0/0.  Substituting 50 or 100 would
        # invent a reading the data cannot support, so this stays unavailable.
        return None
    rsv = (close - low) / span * 100
    k = k_prev + (rsv - k_prev) / KDJ_SMOOTHING_PERIODS
    d = d_prev + (k - d_prev) / KDJ_SMOOTHING_PERIODS
    return {
        "k": round(k, 4),
        "d": round(d, 4),
        "j": round(3 * k - 2 * d, 4),
        "rsv": round(rsv, 4),
        "window_high": high,
        "window_low": low,
        "seed_trade_date": seed.get("trade_date"),
        "basis": seed.get("basis"),
    }


def realtime_indicators(
    factor_row: Mapping[str, Any] | None,
    *,
    price: Any,
    prior_sessions: Iterable[Mapping[str, Any]] = (),
    session_high: Any = None,
    session_low: Any = None,
    basis: str = "bfq",
) -> dict[str, Any]:
    """One live MACD/KDJ reading, with the reason attached when it is absent.

    Degrades per indicator rather than as a whole: a KDJ window short of nine
    sessions must not suppress a MACD reading that needs no window at all.

    Every path returns the same keys.  A payload whose shape follows its own
    status forces each consumer to guess which fields exist this time, and the
    scan aggregates these across the whole basket where most entries are the
    unavailable ones.

    A seed counts only when it is the symbol's previous session - the last of
    ``prior_sessions``.  Advancing an older seed one step answers for a day
    that never followed it: on 2026-10-09 the newest ``stk_factor_pro`` rows
    were from 2026-09-17, three weeks of sessions earlier, and every reading
    built on them looked complete.  Those report ``seed_stale`` instead.
    """
    _require_basis(basis)
    prior_sessions = list(prior_sessions)

    def reading(status: str, *, reason: str | None = None,
                macd: dict[str, Any] | None = None, kdj: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "status": status,
            "reason": reason,
            "basis": basis,
            "seed_trade_date": (macd or kdj or {}).get("seed_trade_date"),
            "price": _number(price),
            "macd": macd,
            "kdj": kdj,
            "degraded": [name for name, value in (("macd", macd), ("kdj", kdj)) if value is None],
            "live_effect": "none",
        }

    if not factor_row:
        return reading("seed_unavailable", reason=f"no seed: fewer than {MIN_SEED_SESSIONS} completed sessions on file")
    if _number(price) is None:
        return reading("price_unavailable", reason="live price is not numeric")
    macd = advance_macd(macd_seed(factor_row, basis=basis), price)
    bounds = kdj_window_bounds(prior_sessions, session_high=session_high, session_low=session_low)
    kdj = advance_kdj(
        kdj_seed(factor_row, basis=basis), price=price,
        window_high=bounds[0] if bounds else None, window_low=bounds[1] if bounds else None,
    ) if bounds else None
    if macd is None and kdj is None:
        return reading("seed_unavailable", reason="the seed row carries no usable factor")
    seed_day = _trade_date_key(factor_row.get("trade_date"))
    previous = _trade_date_key(prior_sessions[-1].get("trading_date")) if prior_sessions else None
    if previous is None or seed_day != previous:
        return reading("seed_stale", reason=(f"the seed is from {seed_day}; "
                                             f"the previous session is {previous or 'unknown'}"))
    return reading("completed", macd=macd, kdj=kdj)


def computed_seed(bars: Sequence[Mapping[str, Any]], *, basis: str = "bfq") -> dict[str, Any] | None:
    """The last bar's seed, computed from a symbol's unadjusted daily bars (oldest first).

    Shaped like the published row ``macd_seed`` and ``kdj_seed`` parse, so they
    read it unchanged.  Fewer than ``MIN_SEED_SESSIONS`` bars, a bar without
    its prices, or any basis but ``bfq`` (the bars are unadjusted) yields None.
    """
    _require_basis(basis)
    if basis != "bfq" or len(bars) < MIN_SEED_SESSIONS:
        return None
    closes: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    for bar in bars:
        close, high, low = _number(bar.get("close")), _number(bar.get("high")), _number(bar.get("low"))
        if close is None or high is None or low is None:
            return None
        closes.append(close)
        highs.append(high)
        lows.append(low)
    ema_fast = ema_slow = closes[0]
    dea = 0.0
    k = d = 50.0
    for index, close in enumerate(closes):
        if index:
            ema_fast += _alpha(MACD_FAST_PERIODS) * (close - ema_fast)
            ema_slow += _alpha(MACD_SLOW_PERIODS) * (close - ema_slow)
            dea += _alpha(MACD_SIGNAL_PERIODS) * (ema_fast - ema_slow - dea)
        window = slice(max(0, index - KDJ_WINDOW_SESSIONS + 1), index + 1)
        high, low = max(highs[window]), min(lows[window])
        # A flat window has no RSV; K and D carry, as advance_kdj declines to invent one.
        if high > low:
            k += ((close - low) / (high - low) * 100 - k) / KDJ_SMOOTHING_PERIODS
            d += (k - d) / KDJ_SMOOTHING_PERIODS
    return {
        "trade_date": _trade_date_key(bars[-1].get("trading_date")),
        f"expma_12_{basis}": round(ema_fast, 6), f"macd_dif_{basis}": round(ema_fast - ema_slow, 6),
        f"macd_dea_{basis}": round(dea, 6), f"kdj_k_{basis}": round(k, 6), f"kdj_d_{basis}": round(d, 6),
        "seed_source": "computed_from_canonical_bars", "seed_sessions": len(bars),
    }


def daily_bars_by_symbol(
    symbols: Iterable[str], connection: Any, *, before_trading_date: Any,
    known_at: Any = None, sessions: int = SEED_HISTORY_SESSIONS,
) -> dict[str, list[dict[str, Any]]]:
    """Each symbol's last ``sessions`` completed unsuspended bars, oldest first, in one read.

    The seed and the RSV window both come from these, so a basket costs one
    query.  ``known_at`` limits a replay to the bars that had landed by then.
    """
    requested = sorted({str(symbol) for symbol in symbols if str(symbol)})
    if not requested:
        return {}
    clause = " AND available_at<=%s" if known_at is not None else ""
    arguments = (requested, before_trading_date, *((known_at,) if known_at is not None else ()), sessions)
    rows = connection.execute(
        f"""WITH ranked AS (
               SELECT symbol,trading_date,close,high,low,
                      row_number() OVER(PARTITION BY symbol ORDER BY trading_date DESC) AS row_number
                 FROM quant.canonical_bars_daily
                WHERE symbol=ANY(%s) AND trading_date<%s AND is_suspended=false{clause}
           )
           SELECT symbol,trading_date,close,high,low FROM ranked
            WHERE row_number<=%s ORDER BY symbol,trading_date ASC""",
        arguments,
    ).fetchall()
    # Pre-seeded so a symbol with no bars still answers, and appended through
    # setdefault so an unexpected symbol cannot raise on the live scan path.
    grouped: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in requested}
    for row in rows:
        payload = dict(row)
        grouped.setdefault(str(payload.pop("symbol")), []).append(payload)
    return grouped


#: One read per symbol per session: a seed cannot change until the next close.
_SESSION_BARS: dict[str, dict[str, list[dict[str, Any]]]] = {}


def _session_bars(symbols: list[str], connection: Any, *, trading_date: Any,
                  known_at: Any) -> dict[str, list[dict[str, Any]]]:
    if known_at is not None:
        return daily_bars_by_symbol(symbols, connection, before_trading_date=trading_date, known_at=known_at)
    key = _trade_date_key(trading_date) or str(trading_date)
    for stale in [day for day in _SESSION_BARS if day != key]:
        del _SESSION_BARS[stale]
    cached = _SESSION_BARS.setdefault(key, {})
    missing = [symbol for symbol in symbols if symbol not in cached]
    if missing:
        cached.update(daily_bars_by_symbol(missing, connection, before_trading_date=trading_date))
    return {symbol: cached.get(symbol, []) for symbol in symbols}


def realtime_indicators_by_symbol(
    quotes: Mapping[str, Mapping[str, Any]], connection: Any, *,
    trading_date: Any, basis: str = "bfq", known_at: Any = None,
) -> dict[str, dict[str, Any]]:
    """Live readings for a watch basket from at most one read per session.

    ``quotes`` maps each symbol to its running ``price``, ``session_high`` and
    ``session_low``.  Symbols whose seed is missing still get an entry, because
    a caller needs to tell "no reading" apart from "symbol not scanned".
    """
    _require_basis(basis)
    symbols = sorted({str(symbol) for symbol in quotes if str(symbol)})
    if not symbols:
        return {}
    bars = _session_bars(symbols, connection, trading_date=trading_date, known_at=known_at)
    result: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        quote = quotes.get(symbol) or {}
        history = bars.get(symbol, [])
        reading = realtime_indicators(
            computed_seed(history, basis=basis), price=quote.get("price"),
            prior_sessions=history[-(KDJ_WINDOW_SESSIONS - 1):],
            session_high=quote.get("session_high"), session_low=quote.get("session_low"), basis=basis,
        )
        result[symbol] = {"symbol": symbol, "trading_date": str(trading_date), **reading}
    return result


def symbol_realtime_indicators(
    symbol: str, connection: Any, *,
    price: Any, session_high: Any, session_low: Any, trading_date: Any,
    basis: str = "bfq", known_at: Any = None,
) -> dict[str, Any]:
    """One symbol's live reading; the basket path with a basket of one."""
    return realtime_indicators_by_symbol(
        {symbol: {"price": price, "session_high": session_high, "session_low": session_low}}, connection,
        trading_date=trading_date, basis=basis, known_at=known_at,
    )[symbol]


def _trade_date_key(value: Any) -> str | None:
    """``tushare_raw_records`` stores ``trade_date`` as YYYYMMDD text."""
    if value is None:
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%Y%m%d")
    digits = str(value).replace("-", "").strip()
    return digits if digits.isdigit() and len(digits) == 8 else None


__all__ = [
    "ADJUSTMENT_BASES", "KDJ_SMOOTHING_PERIODS", "KDJ_WINDOW_SESSIONS",
    "MACD_FAST_PERIODS", "MACD_SIGNAL_PERIODS", "MACD_SLOW_PERIODS", "MIN_SEED_SESSIONS", "SEED_HISTORY_SESSIONS",
    "advance_kdj", "advance_macd", "computed_seed", "daily_bars_by_symbol", "kdj_seed", "kdj_window_bounds",
    "macd_seed", "realtime_indicators", "realtime_indicators_by_symbol", "symbol_realtime_indicators",
]
