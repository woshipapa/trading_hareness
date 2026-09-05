"""Shared availability-clock helpers for research feature materialization.

Exchange dates describe *which session* a row belongs to.  They do not say
when the row was available to a strategy.  This small module gives all local
feature readers one timezone-aware default and rejects ambiguous cutoffs.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Final
from zoneinfo import ZoneInfo


CN_TZ: Final = ZoneInfo("Asia/Shanghai")


def exchange_day_end(exchange_date: date) -> datetime:
    """Return the inclusive end of an exchange day in Shanghai time."""

    return datetime.combine(exchange_date, time.max, tzinfo=CN_TZ)


def availability_cutoff(exchange_date: date, available_before: datetime | None = None) -> datetime:
    """Normalize a PIT cutoff, requiring an explicit timezone when supplied.

    Daily callers that only have an exchange date receive that day's inclusive
    end.  Intraday replay callers must provide an aware timestamp; silently
    interpreting a naive timestamp in the machine timezone would make the
    same experiment produce different feature rows on different hosts.
    """

    if available_before is None:
        return exchange_day_end(exchange_date)
    if available_before.tzinfo is None or available_before.utcoffset() is None:
        raise ValueError("availability cutoff must be timezone-aware")
    return available_before


__all__ = ["CN_TZ", "availability_cutoff", "exchange_day_end"]
