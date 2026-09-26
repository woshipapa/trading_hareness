"""Persisted-evidence settlement for confirmed intraday signal events.

Prices come from what the scans stored: the per-scan watch tape (every
watched stock, every scan) and the priced quote rows of the providers the
scan uses (Longhu, Tencent, the Fuyao all-A snapshot).  Order-book rows are
excluded - their price is not a trade.  Settlement used to read Tencent rows
only, so after Longhu became the primary quote nearly every signal had no
entry or exit price and was skipped (2026-09-21: 40 alerts, 0 outcomes).
Intraday horizons are counted in trading time and never use a lunch or
overnight price.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Callable
from zoneinfo import ZoneInfo

from psycopg.types.json import Json

from .intraday_clock import continuous_auction_bounds, intraday_outcome_window


INTRADAY_EXIT_QUOTE_TOLERANCE_SECONDS = 90
PRICED_QUOTE_SOURCES = ("longhuvip", "tencent_free", "fuyao_ths")
_CN = ZoneInfo("Asia/Shanghai")


class PricePaths:
    """Per (symbol, exchange day): sorted (observed_at, price) from the tape and priced quote rows."""

    def __init__(self, connection: Any, cutoff: datetime) -> None:
        self.connection, self.cutoff = connection, cutoff
        self._tape: dict[date, dict[str, list[tuple[datetime, Decimal]]]] = {}
        self._series: dict[tuple[str, date], tuple[list[datetime], list[Decimal]]] = {}

    def _day_tape(self, day: date) -> dict[str, list[tuple[datetime, Decimal]]]:
        if day not in self._tape:
            start = datetime.combine(day, time(0), tzinfo=_CN)
            rows = self.connection.execute(
                """SELECT r.effective_at, e.key AS symbol, (e.value->>'p')::numeric AS price
                     FROM quant.raw_market_observations r CROSS JOIN LATERAL jsonb_each(r.payload->'rows') e
                    WHERE r.provider_key='quant_scan' AND r.capability='watch_scan_tape' AND r.market='cn'
                      AND r.symbol='watch:scan' AND r.effective_at>=%s AND r.effective_at<%s AND r.effective_at<=%s
                      AND e.value ? 'p'""",
                (start, start + timedelta(days=1), self.cutoff),
            ).fetchall()
            grouped: dict[str, list[tuple[datetime, Decimal]]] = {}
            for row in rows:
                if row["price"] is not None and Decimal(row["price"]) > 0:
                    grouped.setdefault(str(row["symbol"]), []).append((row["effective_at"], Decimal(row["price"])))
            self._tape[day] = grouped
        return self._tape[day]

    def series(self, symbol: str, at: datetime) -> tuple[list[datetime], list[Decimal]]:
        day = at.astimezone(_CN).date()
        key = (symbol, day)
        if key not in self._series:
            start = datetime.combine(day, time(0), tzinfo=_CN)
            quotes = self.connection.execute(
                """SELECT observed_at,price FROM quant.intraday_quote_observations
                     WHERE symbol=%s AND source_name=ANY(%s) AND observed_at>=%s AND observed_at<%s AND observed_at<=%s
                       AND price>0""",
                (symbol, list(PRICED_QUOTE_SOURCES), start, start + timedelta(days=1), self.cutoff),
            ).fetchall()
            merged: dict[datetime, Decimal] = {row["observed_at"]: Decimal(row["price"]) for row in quotes}
            merged.update(dict(self._day_tape(day).get(symbol, [])))
            ordered = sorted(merged.items())
            self._series[key] = ([at for at, _ in ordered], [price for _, price in ordered])
        return self._series[key]

    def last_at_or_before(self, symbol: str, at: datetime, not_before: datetime) -> tuple[datetime, Decimal] | None:
        times, prices = self.series(symbol, at)
        index = bisect_right(times, at) - 1
        return (times[index], prices[index]) if index >= 0 and times[index] >= not_before else None

    def first_in(self, symbol: str, start: datetime, end: datetime) -> tuple[datetime, Decimal] | None:
        times, prices = self.series(symbol, start)
        index = bisect_left(times, start)
        return (times[index], prices[index]) if index < len(times) and times[index] <= end else None

    def between(self, symbol: str, start: datetime, end: datetime, *, include_start: bool = True) -> list[tuple[datetime, Decimal]]:
        times, prices = self.series(symbol, start)
        low = bisect_left(times, start) if include_start else bisect_right(times, start)
        high = bisect_right(times, end)
        return list(zip(times[low:high], prices[low:high]))


def signal_entry_price(evidence: dict[str, Any], decimal_or_none: Callable[[Any], Decimal | None]) -> tuple[Decimal | None, str | None]:
    """The price the signal saw: the scan quote (legacy key ``tencent``, or ``quote``), or a strategy's own ``price``."""
    for source, value in (("signal_evidence.tencent.price", (evidence.get("tencent") or {}).get("price")),
                          ("signal_evidence.quote.price", (evidence.get("quote") or {}).get("price")),
                          ("signal_evidence.price", evidence.get("price"))):
        price = decimal_or_none(value)
        if price is not None and price > 0:
            return price, source
    return None, None


def settle(
    connection: Any, as_of_date: date | None, *, cutoff: datetime,
    horizons: tuple[tuple[str, int], ...], direction_for: Callable[[str], int | None],
    metrics_for: Callable[[Decimal, int, list[Decimal]], dict[str, Decimal] | None],
    decimal_or_none: Callable[[Any], Decimal | None], barrier_spec_type: Callable[[], Any],
    triple_barrier_label: Callable[..., Any], persist_barrier_outcome: Callable[..., Any],
    return_decomposition: Callable[..., dict[str, Any]], json_safe: Callable[[Any], Any],
) -> dict[str, Any]:
    """Settle only from rows already persisted before ``cutoff``; never fetch."""
    horizon_counts = {key: 0 for key, _ in horizons}
    matured = pending = 0
    signals = connection.execute(
        """SELECT signal_event_id,symbol,signal_type,observed_at,evidence
             FROM quant.intraday_signal_events
            WHERE state IN ('confirmed','alerted') AND signal_type IN ('entry','watch','reduce','exit')
              AND observed_at<=%s ORDER BY observed_at""", (cutoff,),
    ).fetchall()
    paths = PricePaths(connection, cutoff)
    skipped = 0
    for signal in signals:
        direction = direction_for(str(signal["signal_type"]))
        evidence = signal["evidence"] if isinstance(signal["evidence"], dict) else {}
        symbol = str(signal["symbol"])
        entry_price, entry_source = signal_entry_price(evidence, decimal_or_none)
        entry_observed_at = signal["observed_at"]
        if entry_price is None:
            signal_bounds = continuous_auction_bounds(signal["observed_at"])
            entry_quote = None
            if signal_bounds is not None:
                session_start, _ = signal_bounds
                entry_quote = paths.last_at_or_before(
                    symbol, signal["observed_at"], max(session_start, signal["observed_at"] - timedelta(seconds=90)))
            if entry_quote:
                (entry_observed_at, entry_price), entry_source = entry_quote, "stored_price_path"
        if entry_price is None or direction is None:
            skipped += 1
            continue
        barrier_spec = barrier_spec_type()
        barrier_bounds = continuous_auction_bounds(entry_observed_at)
        barrier_deadline = entry_observed_at + timedelta(minutes=barrier_spec.max_horizon_minutes)
        if barrier_bounds is None:
            barrier_result: dict[str, Any] = {
                "status": "unavailable", "label": None, "reason": "entry_outside_continuous_auction",
            }
            barrier_rows: list[Any] = []
        else:
            _, session_end = barrier_bounds
            barrier_end = min(cutoff, session_end, barrier_deadline)
            barrier_rows = [{"observed_at": at, "price": price}
                            for at, price in paths.between(symbol, entry_observed_at, barrier_end, include_start=False)]
            barrier_result = triple_barrier_label(
                barrier_rows, entry_price=entry_price,
                entry_at=entry_observed_at, spec=barrier_spec,
            )
            # The generic labeler deliberately knows no exchange sessions.  A
            # truncated 60-minute path must not sit pending forever or borrow
            # the afternoon/next-day path on a later settlement run.
            if (barrier_result.get("status") == "pending" and session_end < barrier_deadline
                    and cutoff >= session_end):
                barrier_result = {
                    "status": "unavailable", "label": None,
                    "reason": "barrier_horizon_crosses_continuous_session_boundary",
                    **{key: barrier_result[key] for key in ("last_at", "last_price") if key in barrier_result},
                }
        persist_barrier_outcome(
            connection, signal["signal_event_id"], spec=barrier_spec, entry_at=entry_observed_at,
            entry_price=entry_price, result=barrier_result,
            source_status={
                "path": "stored_scan_tape_and_priced_quotes", "cutoff": cutoff.isoformat(),
                "session_bounded": True,
                "row_count": len(barrier_rows),
                "reason": barrier_result.get("reason"),
            },
        )
        for horizon_key, minutes in horizons:
            window = intraday_outcome_window(
                entry_observed_at, horizon_minutes=minutes, cutoff=cutoff,
                tolerance_seconds=INTRADAY_EXIT_QUOTE_TOLERANCE_SECONDS, trading_time=True,
            )
            exit_quote = None
            # ``unavailable`` after the quote-delay tolerance has elapsed is
            # not permission to skip the original bounded interval.  The
            # quote may already be in the local ledger when a post-close
            # recompute runs.  Only a session-crossing window has no query
            # bounds and must never borrow lunch/overnight data.
            if (window.get("query_start") is not None and window.get("query_end") is not None
                    and window["query_end"] >= window["query_start"]):
                found = paths.first_in(symbol, window["query_start"], window["query_end"])
                exit_quote = {"observed_at": found[0], "price": found[1]} if found else None
            status = "matured" if exit_quote else str(window["status"])
            if exit_quote:
                # Only prices from continuous trading enter the path (never a lunch print).
                path = [price for at, price in paths.between(symbol, signal["observed_at"], exit_quote["observed_at"])
                        if continuous_auction_bounds(at) is not None]
                outcome = metrics_for(entry_price, direction, path or [Decimal(exit_quote["price"])])
                matured += 1
            else:
                outcome = None
                if status == "pending":
                    pending += 1
            connection.execute(
                """INSERT INTO quant.intraday_signal_outcomes(signal_event_id,horizon_key,direction,entry_observed_at,entry_price,
                     exit_observed_at,exit_price,raw_return,maximum_favorable_excursion,maximum_adverse_excursion,status,tradability,source_status)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'observed_quote_only',%s)
                   ON CONFLICT(signal_event_id,horizon_key) DO UPDATE SET exit_observed_at=EXCLUDED.exit_observed_at,
                     exit_price=EXCLUDED.exit_price,raw_return=EXCLUDED.raw_return,
                     maximum_favorable_excursion=EXCLUDED.maximum_favorable_excursion,
                     maximum_adverse_excursion=EXCLUDED.maximum_adverse_excursion,status=EXCLUDED.status,
                     tradability=EXCLUDED.tradability,source_status=EXCLUDED.source_status,calculated_at=now()""",
                (signal["signal_event_id"], horizon_key, direction, entry_observed_at, entry_price,
                 exit_quote["observed_at"] if exit_quote else None, exit_quote["price"] if exit_quote else None,
                 outcome["raw_return"] if outcome else None, outcome["maximum_favorable_excursion"] if outcome else None,
                 outcome["maximum_adverse_excursion"] if outcome else None, status,
                 Json({
                     "entry": entry_source, "exit": "stored_scan_tape_and_priced_quotes",
                     "cutoff": cutoff.isoformat(), "settlement_window": {
                         key: value.isoformat() if isinstance(value, datetime) else value
                         for key, value in window.items()
                     },
                 })),
            )
            horizon_counts[horizon_key] += 1
        signal_date = signal["observed_at"].astimezone(ZoneInfo("Asia/Shanghai")).date()
        same_day_close: Decimal | None = None
        for horizon_key, date_operator in (("close", "="), ("next_close", ">")):
            daily_exit = connection.execute(
                f"""SELECT trading_date,available_at,open,close FROM quant.canonical_bars_daily
                     WHERE symbol=%s AND trading_date {date_operator} %s AND available_at>%s AND available_at<=%s
                     ORDER BY trading_date LIMIT 1""", (signal["symbol"], signal_date, signal["observed_at"], cutoff),
            ).fetchone()
            status = "matured" if daily_exit else "pending"
            outcome = metrics_for(entry_price, direction, [Decimal(daily_exit["close"])]) if daily_exit else None
            if horizon_key == "close" and daily_exit:
                same_day_close = Decimal(daily_exit["close"])
            decomposition = return_decomposition(
                entry_price, direction, same_day_close,
                Decimal(daily_exit["open"]) if horizon_key == "next_close" and daily_exit and daily_exit["open"] else None,
                Decimal(daily_exit["close"]) if horizon_key == "next_close" and daily_exit else None,
            ) if horizon_key == "next_close" else None
            connection.execute(
                """INSERT INTO quant.intraday_signal_outcomes(signal_event_id,horizon_key,direction,entry_observed_at,entry_price,
                     exit_observed_at,exit_price,raw_return,maximum_favorable_excursion,maximum_adverse_excursion,status,tradability,source_status)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'daily_close_reference',%s)
                   ON CONFLICT(signal_event_id,horizon_key) DO UPDATE SET exit_observed_at=EXCLUDED.exit_observed_at,
                     exit_price=EXCLUDED.exit_price,raw_return=EXCLUDED.raw_return,
                     maximum_favorable_excursion=EXCLUDED.maximum_favorable_excursion,
                     maximum_adverse_excursion=EXCLUDED.maximum_adverse_excursion,status=EXCLUDED.status,
                     tradability=EXCLUDED.tradability,source_status=EXCLUDED.source_status,calculated_at=now()""",
                (signal["signal_event_id"], horizon_key, direction, entry_observed_at, entry_price,
                 daily_exit["available_at"] if daily_exit else None, daily_exit["close"] if daily_exit else None,
                 outcome["raw_return"] if outcome else None, outcome["maximum_favorable_excursion"] if outcome else None,
                 outcome["maximum_adverse_excursion"] if outcome else None, status,
                 Json(json_safe({"entry": entry_source, "exit": "canonical_daily_close", "cutoff": cutoff.isoformat(),
                                 "return_decomposition": decomposition}))),
            )
            if status == "matured":
                matured += 1
            else:
                pending += 1
    return {"as_of_date": str(as_of_date) if as_of_date else None, "signals": len(signals),
            "skipped_without_entry_price": skipped,
            "outcome_rows": sum(horizon_counts.values()) + (len(signals) - skipped) * 2,
            "matured": matured, "pending": pending, "intraday_horizons": horizon_counts}


__all__ = ["PRICED_QUOTE_SOURCES", "PricePaths", "settle", "signal_entry_price"]
