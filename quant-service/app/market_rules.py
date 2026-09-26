"""Small, shared A-share market-rule helpers.

These rules are intentionally independent of database access so research,
replay, and execution simulation use the same fallback semantics.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo


def cn_today(now: datetime | None = None) -> date:
    """Return the exchange calendar date, independent of the container TZ."""
    return (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Asia/Shanghai")).date()


# Main-board risk-warning (ST/*ST) stocks moved from a 5% to a 10% daily band
# when the revised SSE/SZSE trading rules took effect on Monday 2026-07-06.
MAIN_BOARD_ST_TEN_PERCENT_FROM = date(2026, 7, 6)
# ChiNext uses 300/301/302 and STAR uses 688/689; both keep a 20% band for
# ST names too, since the ST 5% band was only ever a main-board rule.
REGISTRATION_BOARD_PREFIXES = ("300", "301", "302", "688", "689")
# The Beijing exchange's legacy 4xxxxx/8xxxxx codes and its 920xxx segment.
BEIJING_PREFIXES = ("4", "8", "920")


def as_exchange_date(value: date | datetime | str | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip().replace("-", "")[:8]
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return None


def a_share_board(symbol: str) -> str:
    """Classify a listed A-share code into its price-band board."""
    code, _, exchange = str(symbol).upper().partition(".")
    if exchange == "BJ" or code.startswith(BEIJING_PREFIXES):
        return "beijing"
    if code.startswith(REGISTRATION_BOARD_PREFIXES):
        return "registration"
    return "main"


def a_share_limit_ratio(symbol: str, is_st: bool | None = False,
                        trade_date: date | datetime | str | None = None) -> float:
    """Fallback daily price-band ratio when the exact stk_limit row is absent.

    ``trade_date`` selects the rule in force that session; without it the
    current exchange date is used, which is correct for live checks only.
    Historical callers must pass the bar's date so a pre-2026-07-06 main-board
    ST session keeps its 5% band.
    """
    board = a_share_board(symbol)
    if board == "beijing":
        return 0.30
    if board == "registration":
        return 0.20
    if is_st:
        session = as_exchange_date(trade_date) or cn_today()
        return 0.10 if session >= MAIN_BOARD_ST_TEN_PERCENT_FROM else 0.05
    return 0.10


def is_st_security_name(name: object) -> bool:
    """Identify the exchange's ST prefix without matching incidental letters."""
    value = str(name or "").strip().upper()
    return value.startswith("ST") or value.startswith("*ST")


def china_equity_session(now: datetime | None = None) -> tuple[bool, str]:
    """Return whether a timestamp is within an SSE continuous-auction window."""
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Asia/Shanghai"))
    current_time = local.time()
    if local.weekday() >= 5:
        return False, "SSE is closed on weekends"
    if time(9, 30) <= current_time <= time(11, 30) or time(13, 0) <= current_time <= time(15, 0):
        return True, "within SSE continuous auction session"
    return False, "outside SSE continuous auction sessions (09:30-11:30, 13:00-15:00 Asia/Shanghai)"


def china_equity_observation_session(now: datetime | None = None) -> tuple[bool, str]:
    """Return whether exchange-facing evidence collectors may run.

    The observation window deliberately starts with the opening call auction
    at 09:15.  Strategy confirmation remains gated by
    :func:`china_equity_session` and therefore cannot turn auction evidence
    into a continuous-auction entry signal.
    """
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Asia/Shanghai"))
    current_time = local.time()
    if local.weekday() >= 5:
        return False, "SSE is closed on weekends"
    if time(9, 15) <= current_time <= time(11, 30) or time(13, 0) <= current_time <= time(15, 0):
        return True, "within SSE evidence observation session"
    return False, "outside SSE evidence observation sessions (09:15-11:30, 13:00-15:00 Asia/Shanghai)"


def china_futures_session(now: datetime | None = None) -> tuple[bool, str]:
    """Conservative daytime guard for the configured CFFEX quote probe."""
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Asia/Shanghai"))
    current_time = local.time()
    if local.weekday() >= 5:
        return False, "CFFEX is closed on weekends"
    if time(9, 0) <= current_time <= time(11, 30) or time(13, 30) <= current_time <= time(15, 0):
        return True, "within CFFEX day session"
    return False, "outside CFFEX day sessions (09:00-11:30, 13:30-15:00 Asia/Shanghai)"
