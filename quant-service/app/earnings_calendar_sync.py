"""Ingest the scheduled reporting calendar and prior earnings guidance.

Every selection strategy in this codebase worked from price and volume alone,
so a stock whose interim report is scheduled for tomorrow was indistinguishable
from one with no scheduled event.  That is the single largest blind spot found
while reviewing three names that limit-up'd on 2026-08-26: two of them had
their half-year report registered for exactly that date, visible in the
exchange calendar the day before.

Three Eastmoney datacenter reports are ingested per reporting period.  They
replaced Tushare's ``disclosure_date``/``forecast``/``express`` on 2026-10-09
(decision 0005); the tables and their meaning are unchanged:

``disclosure_date``  RPT_PUBLIC_BS_APPOIN.  ``pre_date`` is the first
                     exchange-registered date, known days in advance;
                     ``modify_date`` the latest rescheduled one; ``actual_date``
                     fills in once the report lands.  Storing all three is what
                     keeps "as of the previous close, who was scheduled for
                     tomorrow" answerable after the fact rather than only in
                     hindsight.
``forecast``         RPT_PUBLIC_OP_NEWPREDICT, 业绩预告 - a guidance range
                     published ahead of the report.  Eastmoney splits one
                     announcement into a row per indicator; the parent net
                     profit row is the one kept, as Tushare's ``forecast`` held,
                     in the same 10k-CNY unit.
``express``          RPT_FCI_PERFORMANCEE, 业绩快报 - preliminary actual
                     results, also ahead of it, in CNY.

The last two are stored to establish *what the market already knew*, not to
predict a report's contents.  Measured over 2026-07-20..2026-08-25 across 27
sessions (see disclosure_day_watch.py), liquid scheduled disclosers carrying
prior guidance limit-up at 1.60% - indistinguishable from the 1.65% base rate -
while those with no prior guidance reach 3.77%.  Guidance is priced when it is
published, so it removes the surprise rather than signalling one.

Each report is fetched whole - every page, checked against the report's own
row count - or not at all, so a partial answer is never stitched into a
synthetic cross-section.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable

from psycopg.types.json import Json

from .datasources.sources.eastmoney_datacenter import row_symbol


CALENDAR_APIS = ("disclosure_date", "forecast", "express")
#: The Eastmoney datacenter report behind each calendar API.
REPORT_KEYS = {"disclosure_date": "disclosure_schedule", "forecast": "earnings_forecast",
               "express": "earnings_express"}
PROVIDER = "eastmoney_datacenter"
#: Eastmoney's indicator code for 归属于上市公司股东的净利润.
PARENT_NET_PROFIT = "004"
#: A report cannot be scheduled for a period that has not ended yet, and the
#: calendar for a just-ended period takes days to populate.
PERIOD_SETTLE_DAYS = 10


def reporting_period(as_of_date: date, *, settle_days: int = PERIOD_SETTLE_DAYS) -> date:
    """Return the most recent quarter end whose calendar is worth fetching.

    Quarter ends are the only valid ``period``/``end_date`` values for these
    APIs.  A quarter that ended within ``settle_days`` is skipped because its
    disclosure calendar is not registered yet, and asking for it returns an
    almost-empty cross-section that would look like a provider failure.
    """
    year, quarter_ends = as_of_date.year, ((3, 31), (6, 30), (9, 30), (12, 31))
    candidates = [date(year - 1, 12, 31)] + [date(year, month, day) for month, day in quarter_ends]
    eligible = [period for period in candidates if (as_of_date - period).days >= settle_days]
    return max(eligible)


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _ten_thousand(value: Any) -> float | None:
    amount = _number(value)
    return round(amount / 10_000, 4) if amount is not None else None


def _text(value: Any, limit: int = 4000) -> str | None:
    text = str(value).strip() if value not in (None, "") else ""
    return text[:limit] or None


def _day(value: Any) -> date | None:
    """Eastmoney writes dates as ``YYYY-MM-DD 00:00:00``."""
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def normalize_disclosure_rows(rows: list[dict[str, Any]], period: date) -> list[dict[str, Any]]:
    """Keep one row per symbol for the requested period only."""
    by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = row_symbol(row)
        if not symbol or _day(row.get("REPORT_DATE")) != period:
            continue
        changes = [_day(row.get(key)) for key in ("FIRST_CHANGE_DATE", "SECOND_CHANGE_DATE", "THIRD_CHANGE_DATE")]
        changes = [day for day in changes if day is not None]
        by_symbol[symbol] = {
            "symbol": symbol, "period": period,
            "pre_date": _day(row.get("FIRST_APPOINT_DATE")),
            "actual_date": _day(row.get("ACTUAL_PUBLISH_DATE")),
            # The latest reschedule is the date the company now intends.
            "modify_date": changes[-1] if changes else None,
            "raw": dict(row),
        }
    return list(by_symbol.values())


def normalize_forecast_rows(rows: list[dict[str, Any]], period: date) -> list[dict[str, Any]]:
    """One row per announcement, from its parent-net-profit indicator when it has one."""
    by_notice: dict[tuple[str, date], list[dict[str, Any]]] = {}
    for row in rows:
        symbol, notice = row_symbol(row), _day(row.get("NOTICE_DATE"))
        if not symbol or notice is None or _day(row.get("REPORT_DATE")) != period:
            continue
        by_notice.setdefault((symbol, notice), []).append(row)
    first_notice: dict[str, date] = {}
    for symbol, notice in by_notice:
        first_notice[symbol] = min(notice, first_notice.get(symbol, notice))
    normalized: list[dict[str, Any]] = []
    for (symbol, notice), items in by_notice.items():
        row = next((item for item in items if str(item.get("PREDICT_FINANCE_CODE")) == PARENT_NET_PROFIT), items[0])
        profit = str(row.get("PREDICT_FINANCE_CODE")) == PARENT_NET_PROFIT
        normalized.append({
            "symbol": symbol, "period": period, "ann_date": notice,
            "forecast_type": _text(row.get("PREDICT_TYPE"), 64),
            "p_change_min": _number(row.get("ADD_AMP_LOWER")), "p_change_max": _number(row.get("ADD_AMP_UPPER")),
            "net_profit_min": _ten_thousand(row.get("PREDICT_AMT_LOWER")) if profit else None,
            "net_profit_max": _ten_thousand(row.get("PREDICT_AMT_UPPER")) if profit else None,
            "last_parent_net": _ten_thousand(row.get("PREYEAR_SAME_PERIOD")) if profit else None,
            "first_ann_date": first_notice[symbol],
            "summary": _text(row.get("PREDICT_CONTENT")), "change_reason": _text(row.get("CHANGE_REASON_EXPLAIN")),
            "raw": dict(row),
        })
    return normalized


def normalize_express_rows(rows: list[dict[str, Any]], period: date) -> list[dict[str, Any]]:
    normalized: dict[tuple[str, date], dict[str, Any]] = {}
    for row in rows:
        symbol = row_symbol(row)
        notice = _day(row.get("NOTICE_DATE")) or _day(row.get("UPDATE_DATE"))
        if not symbol or notice is None or _day(row.get("REPORT_DATE")) != period:
            continue
        normalized[(symbol, notice)] = {
            "symbol": symbol, "period": period, "ann_date": notice,
            "revenue": _number(row.get("TOTAL_OPERATE_INCOME")), "operate_profit": None,
            "total_profit": None, "n_income": _number(row.get("PARENT_NETPROFIT")),
            "total_assets": None, "diluted_eps": _number(row.get("BASIC_EPS")),
            "diluted_roe": _number(row.get("WEIGHTAVG_ROE")), "yoy_net_profit": _number(row.get("JLRTBZCL")),
            "perf_summary": _text(row.get("DATATYPE")), "raw": dict(row),
        }
    return list(normalized.values())


NORMALIZERS: dict[str, Callable[..., list[dict[str, Any]]]] = {
    "disclosure_date": normalize_disclosure_rows,
    "forecast": normalize_forecast_rows,
    "express": normalize_express_rows,
}


def persist_disclosure_schedule(connection: Any, rows: list[dict[str, Any]], provider: str,
                                available_at: datetime) -> int:
    for row in rows:
        connection.execute(
            """INSERT INTO quant.disclosure_schedule(
                    symbol,period,provider,pre_date,actual_date,modify_date,available_at,raw)
               SELECT %s,%s,%s,%s,%s,%s,%s,%s
                WHERE EXISTS(SELECT 1 FROM quant.instruments WHERE symbol=%s)
               ON CONFLICT(symbol,period,provider) DO UPDATE SET
                 pre_date=EXCLUDED.pre_date,actual_date=EXCLUDED.actual_date,
                 modify_date=EXCLUDED.modify_date,available_at=EXCLUDED.available_at,raw=EXCLUDED.raw""",
            (row["symbol"], row["period"], provider, row["pre_date"], row["actual_date"],
             row["modify_date"], available_at, Json(row["raw"]), row["symbol"]),
        )
    return len(rows)


def persist_earnings_forecasts(connection: Any, rows: list[dict[str, Any]], provider: str,
                               available_at: datetime) -> int:
    for row in rows:
        connection.execute(
            """INSERT INTO quant.earnings_forecasts(
                    symbol,period,ann_date,provider,forecast_type,p_change_min,p_change_max,
                    net_profit_min,net_profit_max,last_parent_net,first_ann_date,summary,change_reason,
                    available_at,raw)
               SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                WHERE EXISTS(SELECT 1 FROM quant.instruments WHERE symbol=%s)
               ON CONFLICT(symbol,period,ann_date,provider) DO UPDATE SET
                 forecast_type=EXCLUDED.forecast_type,p_change_min=EXCLUDED.p_change_min,
                 p_change_max=EXCLUDED.p_change_max,net_profit_min=EXCLUDED.net_profit_min,
                 net_profit_max=EXCLUDED.net_profit_max,last_parent_net=EXCLUDED.last_parent_net,
                 first_ann_date=EXCLUDED.first_ann_date,summary=EXCLUDED.summary,
                 change_reason=EXCLUDED.change_reason,available_at=EXCLUDED.available_at,raw=EXCLUDED.raw""",
            (row["symbol"], row["period"], row["ann_date"], provider, row["forecast_type"],
             row["p_change_min"], row["p_change_max"], row["net_profit_min"], row["net_profit_max"],
             row["last_parent_net"], row["first_ann_date"], row["summary"], row["change_reason"],
             available_at, Json(row["raw"]), row["symbol"]),
        )
    return len(rows)


def persist_earnings_express(connection: Any, rows: list[dict[str, Any]], provider: str,
                             available_at: datetime) -> int:
    for row in rows:
        connection.execute(
            """INSERT INTO quant.earnings_express(
                    symbol,period,ann_date,provider,revenue,operate_profit,total_profit,n_income,
                    total_assets,diluted_eps,diluted_roe,yoy_net_profit,perf_summary,available_at,raw)
               SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                WHERE EXISTS(SELECT 1 FROM quant.instruments WHERE symbol=%s)
               ON CONFLICT(symbol,period,ann_date,provider) DO UPDATE SET
                 revenue=EXCLUDED.revenue,operate_profit=EXCLUDED.operate_profit,
                 total_profit=EXCLUDED.total_profit,n_income=EXCLUDED.n_income,
                 total_assets=EXCLUDED.total_assets,diluted_eps=EXCLUDED.diluted_eps,
                 diluted_roe=EXCLUDED.diluted_roe,yoy_net_profit=EXCLUDED.yoy_net_profit,
                 perf_summary=EXCLUDED.perf_summary,available_at=EXCLUDED.available_at,raw=EXCLUDED.raw""",
            (row["symbol"], row["period"], row["ann_date"], provider, row["revenue"], row["operate_profit"],
             row["total_profit"], row["n_income"], row["total_assets"], row["diluted_eps"],
             row["diluted_roe"], row["yoy_net_profit"], row["perf_summary"], available_at,
             Json(row["raw"]), row["symbol"]),
        )
    return len(rows)


PERSISTERS: dict[str, Callable[..., int]] = {
    "disclosure_date": persist_disclosure_schedule,
    "forecast": persist_earnings_forecasts,
    "express": persist_earnings_express,
}


async def sync(
    as_of_date: date,
    *,
    fetch_period_report: Callable[[str, date], Awaitable[list[dict[str, Any]]]],
    run_database_blocking: Callable[..., Awaitable[Any]],
    db: Any,
    safe_error_detail: Callable[[str, int], str],
    period: date | None = None,
) -> dict[str, Any]:
    """Fetch one reporting period's calendar and guidance, then promote it whole.

    Each report is independent: a failure of one is reported and skipped rather
    than blocking the others, because the disclosure calendar is useful on its
    own and guidance is useful on its own.  An empty ``express`` response is
    legitimate - very few companies publish one - so emptiness is never
    treated as a failure here.
    """
    target_period = period or reporting_period(as_of_date)
    stamp = target_period.strftime("%Y%m%d")
    observed_at = datetime.now(timezone.utc)
    fetched: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for api_name in CALENDAR_APIS:
        try:
            raw_rows = await fetch_period_report(REPORT_KEYS[api_name], target_period)
        except Exception as error:  # one report's outage must not hide the others
            errors[api_name] = safe_error_detail(str(error), 300)
            continue
        fetched[api_name] = {"provider": PROVIDER, "rows": NORMALIZERS[api_name](raw_rows, target_period)}
    if not fetched:
        return {"status": "blocked", "as_of_date": str(as_of_date), "period": str(target_period),
                "reason": "every reporting-calendar report failed", "errors": errors}

    def persist() -> dict[str, int]:
        stored: dict[str, int] = {}
        with db.transaction() as connection:
            for api_name, payload in fetched.items():
                stored[api_name] = PERSISTERS[api_name](
                    connection, payload["rows"], payload["provider"], observed_at,
                )
        return stored

    stored = await run_database_blocking(persist, timeout_seconds=180)
    request_key = hashlib.sha256(
        json.dumps({"capability": "earnings_calendar", "period": stamp}, sort_keys=True).encode(),
    ).hexdigest()
    return {"status": "completed" if not errors else "partial", "as_of_date": str(as_of_date),
            "period": str(target_period), "rows": stored,
            "providers": {name: payload["provider"] for name, payload in fetched.items()},
            "errors": errors or None, "request_key": request_key}


__all__ = [
    "CALENDAR_APIS", "NORMALIZERS", "PERIOD_SETTLE_DAYS", "PERSISTERS", "PROVIDER", "REPORT_KEYS",
    "normalize_disclosure_rows", "normalize_express_rows", "normalize_forecast_rows",
    "persist_disclosure_schedule", "persist_earnings_express", "persist_earnings_forecasts",
    "reporting_period", "sync",
]
