"""Advance MACD/KDJ from a published daily factor seed to a live price.

Recomputing these from local history is not an option.  ``canonical_bars_daily``
keeps a bounded hot window - full-market coverage reaches back about two weeks
and 600176.SH carries 18 bars - while a converged MACD(12,26,9) needs roughly
60-100.  ``stk_factor_pro`` publishes converged daily values built on the
vendor's own full history, so the live value is obtained by seeding from the
last published session and advancing a single step with the running price,
which needs no local history at all.

``stk_factor_pro`` does not publish EMA26.  It publishes EXPMA12 and DIF, and
DIF = EMA12 - EMA26, so ``EMA26 = EXPMA12 - DIF`` recovers the missing state.
Every formula and constant here was checked against the vendor's own
consecutive rows for 600176.SH (2026-09-10..2026-09-16): the histogram is
2*(DIF-DEA), DEA is a 9-period EMA of DIF, EXPMA12 advances with alpha 2/13,
D is a 3-period smoothing of K, and RSV's window counts the current session's
own high/low.  Seeding EMA26 through DIF inherits DIF's published precision
(three decimals), which moves the next DIF by well under 0.001 - immaterial
against a histogram that moves in tenths.

Seed and live price must share one adjustment basis.  Live quotes are
unadjusted, so ``bfq`` is the default; pairing a ``qfq`` seed with an
unadjusted price silently corrupts every value from the next ex-dividend date
onward, and nothing downstream would flag it.

Research-only: these are observation features, not an order instruction.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

MACD_FAST_PERIODS = 12
MACD_SLOW_PERIODS = 26
MACD_SIGNAL_PERIODS = 9
KDJ_WINDOW_SESSIONS = 9
KDJ_SMOOTHING_PERIODS = 3

#: ``stk_factor_pro`` publishes every factor once per adjustment basis.
ADJUSTMENT_BASES = ("bfq", "qfq", "hfq")


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
    """
    _require_basis(basis)

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
        return reading("seed_unavailable", reason="no published factor row")
    if _number(price) is None:
        return reading("price_unavailable", reason="live price is not numeric")
    macd = advance_macd(macd_seed(factor_row, basis=basis), price)
    bounds = kdj_window_bounds(prior_sessions, session_high=session_high, session_low=session_low)
    kdj = advance_kdj(
        kdj_seed(factor_row, basis=basis), price=price,
        window_high=bounds[0] if bounds else None, window_low=bounds[1] if bounds else None,
    ) if bounds else None
    if macd is None and kdj is None:
        return reading("seed_unavailable", reason="the published row carries no usable factor")
    return reading("completed", macd=macd, kdj=kdj)


def seed_factor_keys(basis: str = "bfq") -> list[str]:
    """The keys a row must carry before it can seed anything at all."""
    _require_basis(basis)
    return [f"expma_12_{basis}", f"kdj_k_{basis}"]


def latest_factor_row(
    symbol: str, connection: Any, *, before_trading_date: Any,
    known_at: Any = None, basis: str = "bfq",
) -> dict[str, Any] | None:
    """Read the newest usable factor row strictly before the live session.

    ``before_trading_date`` is not an optimisation.  Once the vendor publishes
    the live session's own factors that evening, seeding from them and then
    advancing with that same session's price would count the session twice, and
    the result still looks plausible.  ``known_at`` keeps a replay honest by
    hiding rows that had not been fetched yet at the simulated moment.

    Newest is not enough on its own.  On 2026-09-17 the ProMax GET gateway
    answered ``stk_factor_pro`` for six symbols with a VCP breakout payload
    (``vcp_score``, ``pivot_high_60d``) carrying no factor at all, and stored it
    under this same ``api_name``.  Taking the newest row unconditionally lets
    one such row shadow the good row sitting directly behind it, and the symbol
    then reports ``seed_unavailable`` despite holding a perfectly usable seed.
    Requiring the seed keys in the query skips the impostor instead.
    """
    cutoff = _trade_date_key(before_trading_date)
    if cutoff is None:
        return None
    clause = " AND available_at<=%s" if known_at is not None else ""
    arguments: tuple[Any, ...] = (symbol, cutoff, seed_factor_keys(basis))
    if known_at is not None:
        arguments = (symbol, cutoff, known_at, seed_factor_keys(basis))
    rows = connection.execute(
        f"""SELECT row_data FROM quant.tushare_raw_records
             WHERE api_name='stk_factor_pro' AND row_data->>'ts_code'=%s
               AND row_data->>'trade_date' < %s{clause}
               AND row_data ?| %s::text[]
             ORDER BY row_data->>'trade_date' DESC, available_at DESC LIMIT 1""",
        arguments,
    ).fetchall()
    if not rows:
        return None
    row_data = dict(rows[0]).get("row_data")
    return dict(row_data) if row_data else None


def prior_session_bars(symbol: str, connection: Any, *, before_trading_date: Any) -> list[dict[str, Any]]:
    """The completed sessions the RSV window needs, oldest first."""
    rows = connection.execute(
        """SELECT trading_date,high,low FROM quant.canonical_bars_daily
            WHERE symbol=%s AND trading_date<%s AND is_suspended=false
            ORDER BY trading_date DESC LIMIT %s""",
        (symbol, before_trading_date, KDJ_WINDOW_SESSIONS - 1),
    ).fetchall()
    return [dict(row) for row in reversed(rows)]


def latest_factor_rows_by_symbol(
    symbols: Iterable[str], connection: Any, *, before_trading_date: Any,
    known_at: Any = None, basis: str = "bfq",
) -> dict[str, dict[str, Any]]:
    """One seed per symbol for a whole watch basket in a single read.

    The live scan runs every 10-30 seconds over the full basket, so the
    per-symbol reader's ``LIMIT 1`` would become one round trip per symbol per
    scan.  ``DISTINCT ON`` applies the same ordering - and the same rejection of
    rows carrying no seed - once across the basket.
    """
    requested = sorted({str(symbol) for symbol in symbols if str(symbol)})
    cutoff = _trade_date_key(before_trading_date)
    if not requested or cutoff is None:
        return {}
    clause = " AND available_at<=%s" if known_at is not None else ""
    arguments: tuple[Any, ...] = (requested, cutoff, seed_factor_keys(basis))
    if known_at is not None:
        arguments = (requested, cutoff, known_at, seed_factor_keys(basis))
    rows = connection.execute(
        f"""SELECT DISTINCT ON (row_data->>'ts_code')
                   row_data->>'ts_code' AS symbol, row_data
              FROM quant.tushare_raw_records
             WHERE api_name='stk_factor_pro' AND row_data->>'ts_code'=ANY(%s)
               AND row_data->>'trade_date' < %s{clause}
               AND row_data ?| %s::text[]
             ORDER BY row_data->>'ts_code', row_data->>'trade_date' DESC, available_at DESC""",
        arguments,
    ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = dict(row)
        row_data = payload.get("row_data")
        if row_data:
            result[str(payload.get("symbol"))] = dict(row_data)
    return result


def prior_session_bars_by_symbol(
    symbols: Iterable[str], connection: Any, *, before_trading_date: Any,
) -> dict[str, list[dict[str, Any]]]:
    """The RSV window's completed sessions for a whole basket, oldest first."""
    requested = sorted({str(symbol) for symbol in symbols if str(symbol)})
    if not requested:
        return {}
    rows = connection.execute(
        """WITH ranked AS (
               SELECT symbol,trading_date,high,low,
                      row_number() OVER(PARTITION BY symbol ORDER BY trading_date DESC) AS row_number
                 FROM quant.canonical_bars_daily
                WHERE symbol=ANY(%s) AND trading_date<%s AND is_suspended=false
           )
           SELECT symbol,trading_date,high,low FROM ranked
            WHERE row_number<=%s ORDER BY symbol,trading_date ASC""",
        (requested, before_trading_date, KDJ_WINDOW_SESSIONS - 1),
    ).fetchall()
    # Pre-seeded so a symbol with no bars still answers, and appended through
    # setdefault so an unexpected symbol cannot raise on the live scan path.
    grouped: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in requested}
    for row in rows:
        payload = dict(row)
        grouped.setdefault(str(payload.pop("symbol")), []).append(payload)
    return grouped


def realtime_indicators_by_symbol(
    quotes: Mapping[str, Mapping[str, Any]], connection: Any, *,
    trading_date: Any, basis: str = "bfq", known_at: Any = None,
) -> dict[str, dict[str, Any]]:
    """Live readings for a watch basket from two reads, not two per symbol.

    ``quotes`` maps each symbol to its running ``price``, ``session_high`` and
    ``session_low``.  Symbols whose seed is missing still get an entry, because
    a caller needs to tell "no reading" apart from "symbol not scanned".
    """
    _require_basis(basis)
    symbols = sorted({str(symbol) for symbol in quotes if str(symbol)})
    if not symbols:
        return {}
    seeds = latest_factor_rows_by_symbol(
        symbols, connection, before_trading_date=trading_date, known_at=known_at, basis=basis)
    bars = prior_session_bars_by_symbol(symbols, connection, before_trading_date=trading_date)
    result: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        quote = quotes.get(symbol) or {}
        reading = realtime_indicators(
            seeds.get(symbol), price=quote.get("price"), prior_sessions=bars.get(symbol, ()),
            session_high=quote.get("session_high"), session_low=quote.get("session_low"), basis=basis,
        )
        result[symbol] = {"symbol": symbol, "trading_date": str(trading_date), **reading}
    return result


def symbol_realtime_indicators(
    symbol: str, connection: Any, *,
    price: Any, session_high: Any, session_low: Any, trading_date: Any,
    basis: str = "bfq", known_at: Any = None,
) -> dict[str, Any]:
    """Compose one symbol's live reading from a caller-owned transaction."""
    factor_row = latest_factor_row(
        symbol, connection, before_trading_date=trading_date, known_at=known_at, basis=basis)
    result = realtime_indicators(
        factor_row, price=price, prior_sessions=prior_session_bars(symbol, connection, before_trading_date=trading_date),
        session_high=session_high, session_low=session_low, basis=basis,
    )
    return {"symbol": symbol, "trading_date": str(trading_date), **result}


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
    "MACD_FAST_PERIODS", "MACD_SIGNAL_PERIODS", "MACD_SLOW_PERIODS",
    "advance_kdj", "advance_macd", "kdj_seed", "kdj_window_bounds",
    "latest_factor_row", "latest_factor_rows_by_symbol", "macd_seed",
    "prior_session_bars", "prior_session_bars_by_symbol", "realtime_indicators",
    "realtime_indicators_by_symbol", "seed_factor_keys", "symbol_realtime_indicators",
]
