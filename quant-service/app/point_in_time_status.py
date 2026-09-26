"""Point-in-time ST status for historical research.

``quant.instruments.is_st`` is today's flag.  Reading it for a past session
labels a stock that was ST then but is not now (or the reverse) with the
wrong status, which changes both its price band and whether a screen keeps
it.  The dated evidence is the daily ``stock_st`` cross-section in
``instrument_lifecycle_evidence`` (``list_status='UNKNOWN'``, one row per ST
name per session).

On a session that cross-section covers, it is the answer: listed means ST,
absent means not ST.  On a session it does not cover yet, the current flag is
the only value available; the reader says so through ``st_basis`` rather than
presenting it as point-in-time.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

ST_EVIDENCE_STATUS = "UNKNOWN"


def pit_st_sql(symbol_expr: str, date_expr: str, current_flag_expr: str) -> str:
    """SQL boolean: the session's ST status, else the current flag."""
    return (
        "CASE WHEN EXISTS (SELECT 1 FROM quant.instrument_lifecycle_evidence st_cov "
        f"WHERE st_cov.status_date={date_expr} AND st_cov.list_status='{ST_EVIDENCE_STATUS}') "
        "THEN EXISTS (SELECT 1 FROM quant.instrument_lifecycle_evidence st_row "
        f"WHERE st_row.symbol={symbol_expr} AND st_row.status_date={date_expr} "
        f"AND st_row.list_status='{ST_EVIDENCE_STATUS}' AND st_row.is_st) "
        f"ELSE {current_flag_expr} END"
    )


def pit_st_basis_sql(date_expr: str) -> str:
    """SQL text: whether the ST flag for this session is dated evidence."""
    return (
        "CASE WHEN EXISTS (SELECT 1 FROM quant.instrument_lifecycle_evidence st_cov "
        f"WHERE st_cov.status_date={date_expr} AND st_cov.list_status='{ST_EVIDENCE_STATUS}') "
        "THEN 'dated_stock_st' ELSE 'current_instrument_flag' END"
    )


def st_flags_as_of(connection: Any, symbols: Iterable[str], as_of_date: date) -> dict[str, dict[str, Any]]:
    """Each symbol's ST status on one session, with the basis it rests on."""
    requested = sorted({str(symbol) for symbol in symbols if symbol})
    if not requested:
        return {}
    rows = connection.execute(
        f"""SELECT i.symbol,
                   {pit_st_sql('i.symbol', '%s::date', 'coalesce(i.is_st,false)')} AS is_st,
                   {pit_st_basis_sql('%s::date')} AS st_basis
              FROM quant.instruments i
             WHERE i.symbol=ANY(%s)""",
        (as_of_date, as_of_date, as_of_date, requested),
    ).fetchall()
    return {str(dict(row)["symbol"]): {"is_st": bool(dict(row)["is_st"]), "st_basis": dict(row)["st_basis"]}
            for row in rows}


__all__ = ["ST_EVIDENCE_STATUS", "pit_st_basis_sql", "pit_st_sql", "st_flags_as_of"]
