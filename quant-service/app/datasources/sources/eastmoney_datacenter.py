"""Allow-listed Eastmoney datacenter reports (corporate events and capital).

The datacenter is one generic ``/api/data/v1/get`` endpoint keyed by a
``reportName``.  Only the reports declared in :data:`REPORTS` may be queried,
each with a fixed host, sort order and filter template.

Point-in-time rule.  Most reports carry only a *date* (notice date, trade
date) and some carry dates in the future (a notice dated tomorrow, an unlock
next month).  None of those is the moment the fact became public.  A row's
``published_at`` is therefore Eastmoney's own ingest clock (``EITIME`` /
``EUTIME``) when present, else the capture time -- never a bare date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from ..http import ashare_symbol, number, request_json


PROVIDER_KEY = "eastmoney_datacenter"
CN_TZ = ZoneInfo("Asia/Shanghai")
_WEB = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_SECURITIES = "https://datacenter.eastmoney.com/securities/api/data/v1/get"


@dataclass(frozen=True)
class ReportSpec:
    key: str
    report_name: str
    url: str
    sort_columns: str
    sort_types: str
    event_type: str | None
    label: str
    #: ``filter(start, end)`` -> the datacenter filter expression.
    window_filter: Callable[[date, date], str] | None = None


def _dates(column: str) -> Callable[[date, date], str]:
    return lambda start, end: f"({column}>='{start.isoformat()}')({column}<='{end.isoformat()}')"


REPORTS: dict[str, ReportSpec] = {spec.key: spec for spec in (
    ReportSpec("restricted_release", "RPT_LIFT_STAGE", _WEB, "FREE_DATE,CURRENT_FREE_SHARES", "1,1",
               "restricted_release_schedule", "限售解禁", _dates("FREE_DATE")),
    ReportSpec("holder_count", "RPT_HOLDERNUMLATEST", _WEB, "HOLD_NOTICE_DATE,SECURITY_CODE", "-1,-1",
               "holder_count_change", "股东户数", _dates("HOLD_NOTICE_DATE")),
    ReportSpec("holder_trade", "RPT_SHARE_HOLDER_INCREASE", _WEB, "EITIME,SECURITY_CODE", "-1,-1",
               "holder_trade", "股东增减持", _dates("NOTICE_DATE")),
    ReportSpec("block_trade", "RPT_DATA_BLOCKTRADE", _WEB, "DEAL_AMT", "-1",
               "block_trade", "大宗交易", _dates("TRADE_DATE")),
    ReportSpec("earnings_forecast", "RPT_PUBLIC_OP_NEWPREDICT", _SECURITIES, "NOTICE_DATE,SECURITY_CODE", "-1,-1",
               "earnings_forecast", "业绩预告", _dates("NOTICE_DATE")),
    ReportSpec("earnings_express", "RPT_FCI_PERFORMANCEE", _SECURITIES, "UPDATE_DATE,SECURITY_CODE", "-1,-1",
               "earnings_express", "业绩快报", _dates("UPDATE_DATE")),
    ReportSpec("repurchase", "RPTA_WEB_GETHGLIST_NEW", _WEB, "UPDATEDATE,DIM_SCODE", "-1,-1",
               "repurchase", "股份回购", _dates("UPDATEDATE")),
    ReportSpec("ipo_calendar", "RPTA_APP_IPOAPPLY", _WEB, "APPLY_DATE,SECURITY_CODE", "1,1",
               "ipo_calendar", "新股申购", _dates("APPLY_DATE")),
    ReportSpec("margin_detail", "RPTA_WEB_RZRQ_GGMX", _WEB, "SCODE", "1",
               None, "融资融券明细", _dates("DATE")),
    ReportSpec("margin_market", "RPTA_RZRQ_LSHJ", _WEB, "DIM_DATE", "-1",
               None, "两融市场汇总", _dates("DIM_DATE")),
    ReportSpec("share_capital", "RPT_F10_EH_EQUITY", _WEB, "END_DATE", "-1",
               None, "股本变动", None),
    # Queried by date or reporting period through ``extra_filter`` (see
    # suspension_filter / period_filter), never by a capture window.
    ReportSpec("suspension", "RPT_CUSTOM_SUSPEND_DATA_INTERFACE", _WEB, "SUSPEND_START_DATE,SECURITY_CODE", "-1,1",
               None, "停复牌", None),
    ReportSpec("disclosure_schedule", "RPT_PUBLIC_BS_APPOIN", _WEB, "FIRST_APPOINT_DATE,SECURITY_CODE", "1,1",
               None, "定期报告预约披露", None),
)}


def suspension_filter(trading_date: date) -> str:
    """Every security suspended on ``trading_date``, including multi-day suspensions begun earlier."""
    return f'(MARKET="全部")(DATETIME=\'{trading_date.isoformat()}\')'


def period_filter(period: date) -> str:
    """One reporting period (a quarter end) of the disclosure, forecast or express report."""
    return f"(REPORT_DATE='{period.isoformat()}')"


def _parse_clock(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if len(text) < 16:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("/", "-"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=CN_TZ)


def _day(value: Any) -> str | None:
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def available_at(row: Mapping[str, Any], observed_at: datetime) -> datetime:
    """Eastmoney's ingest clock, capped at the capture time, else capture time."""
    for key in ("EITIME", "EUTIME"):
        clock = _parse_clock(row.get(key))
        if clock is not None:
            return min(clock, observed_at)
    return observed_at


def row_symbol(row: Mapping[str, Any]) -> str | None:
    return (ashare_symbol(row.get("SECUCODE")) or ashare_symbol(row.get("SECURITY_CODE"))
            or ashare_symbol(row.get("DIM_SCODE")) or ashare_symbol(row.get("SCODE")))


def _name(row: Mapping[str, Any]) -> str:
    return str(row.get("SECURITY_NAME_ABBR") or row.get("SECURITYSHORTNAME") or row.get("SECNAME") or "").strip()


def _title_and_identity(key: str, symbol: str, row: Mapping[str, Any]) -> tuple[str, str]:
    name = _name(row) or symbol
    if key == "restricted_release":
        free_date = _day(row.get("FREE_DATE"))
        shares = number(row.get("CURRENT_FREE_SHARES"))
        kind = str(row.get("FREE_SHARES_TYPE") or "").strip()
        title = f"限售解禁：{name} {free_date} 解禁{shares:g}万股{('（' + kind + '）') if kind else ''}" if shares is not None \
            else f"限售解禁：{name} {free_date}"
        return title, f"{free_date}:{kind}"
    if key == "holder_count":
        end = _day(row.get("END_DATE"))
        ratio = number(row.get("HOLDER_NUM_RATIO"))
        change = f"，较上期{ratio:+.2f}%" if ratio is not None else ""
        return f"股东户数：{name} 截至{end} {int(number(row.get('HOLDER_NUM')) or 0)}户{change}", f"{end}"
    if key == "holder_trade":
        holder = str(row.get("HOLDER_NAME") or "").strip()
        direction = str(row.get("DIRECTION") or "").strip()
        amount = number(row.get("CHANGE_NUM"))
        start, end = _day(row.get("START_DATE")), _day(row.get("END_DATE"))
        title = f"股东{direction or '变动'}：{name} {holder} {amount:g}万股" if amount is not None else f"股东{direction or '变动'}：{name} {holder}"
        return title, f"{holder}:{start}:{end}:{amount}"
    if key == "block_trade":
        trade_date = _day(row.get("TRADE_DATE"))
        price, volume = number(row.get("DEAL_PRICE")), number(row.get("DEAL_VOLUME"))
        premium = number(row.get("PREMIUM_RATIO"))
        title = f"大宗交易：{name} {trade_date} 成交价{price} 溢价率{premium:+.2f}%" if premium is not None else f"大宗交易：{name} {trade_date}"
        return title, f"{trade_date}:{row.get('BUYER_CODE')}:{row.get('SELLER_CODE')}:{price}:{volume}"
    if key == "earnings_forecast":
        report = _day(row.get("REPORT_DATE"))
        kind = str(row.get("PREDICT_TYPE") or row.get("PREDICT_FINANCE") or "").strip()
        return f"业绩预告：{name} {report} {kind}", f"{report}:{row.get('PREDICT_FINANCE_CODE')}:{_day(row.get('NOTICE_DATE'))}"
    if key == "earnings_express":
        report = _day(row.get("REPORT_DATE"))
        return f"业绩快报：{name} {row.get('DATATYPE') or report}", f"{report}:{_day(row.get('NOTICE_DATE') or row.get('UPDATE_DATE'))}"
    if key == "repurchase":
        progress = str(row.get("REPURPROGRESS") or "").strip()
        return f"股份回购：{name} 进度{progress}", f"{row.get('REPURCODE')}:{progress}:{_day(row.get('UPDATEDATE'))}"
    if key == "ipo_calendar":
        apply_date = _day(row.get("APPLY_DATE"))
        return f"新股申购：{name} 申购日{apply_date}", f"{apply_date}"
    raise KeyError(key)


def report_events(key: str, rows: list[Mapping[str, Any]], observed_at: datetime) -> list[dict[str, Any]]:
    """Market-event rows for an event-shaped report; others return ``[]``."""
    spec = REPORTS[key]
    if spec.event_type is None:
        return []
    events = []
    for row in rows:
        symbol = row_symbol(row)
        if symbol is None:
            continue
        title, identity = _title_and_identity(key, symbol, row)
        events.append({
            "ts_code": symbol, "event_type": spec.event_type,
            "published_at": available_at(row, observed_at).isoformat(), "title": title, "url": None,
            "event_identity_key": f"{PROVIDER_KEY}:{spec.event_type}:{symbol}:{identity}",
            "raw": {"capability": key, "report_name": spec.report_name, **dict(row)},
        })
    return events


def report_observations(key: str, rows: list[Mapping[str, Any]], observed_at: datetime) -> list[dict[str, Any]]:
    """Timed raw observations for a non-event report (margin, share capital)."""
    spec = REPORTS[key]
    result = []
    for row in rows:
        symbol = row_symbol(row)
        effective_day = _day(row.get("DATE") or row.get("DIM_DATE") or row.get("END_DATE"))
        effective = datetime.fromisoformat(f"{effective_day}T15:00:00+08:00") if effective_day else observed_at
        result.append({
            "ts_code": symbol, "effective_at": min(effective, observed_at).isoformat(),
            "available_at": available_at(row, observed_at).isoformat(),
            "report_name": spec.report_name, **dict(row),
        })
    return result


async def fetch_report(
    key: str,
    *,
    start: date | None = None,
    end: date | None = None,
    extra_filter: str = "",
    page_size: int = 500,
    max_pages: int = 20,
    require_complete: bool = False,
) -> list[dict[str, Any]]:
    """Fetch one allow-listed report, paging until exhausted or ``max_pages``.

    ``require_complete`` refuses a short answer: when the pages read hold
    fewer rows than the report's own ``count``, it raises instead of
    returning a cross-section that only looks whole.
    """
    spec = REPORTS[key]
    filters = extra_filter
    if spec.window_filter is not None and start is not None:
        filters = spec.window_filter(start, end or start) + filters
    rows: list[dict[str, Any]] = []
    expected: int | None = None
    for page in range(1, max(1, max_pages) + 1):
        params = {
            "reportName": spec.report_name, "columns": "ALL", "source": "WEB", "client": "WEB",
            "sortColumns": spec.sort_columns, "sortTypes": spec.sort_types,
            "pageSize": str(max(1, min(page_size, 500))), "pageNumber": str(page),
        }
        if filters:
            params["filter"] = filters
        payload = await request_json("GET", spec.url, params=params, timeout_seconds=30.0)
        if not isinstance(payload, Mapping):
            raise ValueError("Eastmoney datacenter response is not an object")
        result = payload.get("result")
        if not isinstance(result, Mapping):
            # ``code=9201`` is the datacenter's valid-empty answer.
            if payload.get("code") in (9201, "9201") or payload.get("success") is False:
                break
            raise ValueError("Eastmoney datacenter response has no result")
        data = [dict(row) for row in result.get("data") or [] if isinstance(row, Mapping)]
        if expected is None:
            expected = int(number(result.get("count")) or 0)
        rows.extend(data)
        pages = int(number(result.get("pages")) or 1)
        if page >= pages or not data:
            break
    if require_complete and expected is not None and len(rows) < expected:
        raise ValueError(f"Eastmoney {spec.report_name} returned {len(rows)} of {expected} rows")
    return rows


async def period_report(key: str, period: date) -> list[dict[str, Any]]:
    """One whole reporting period of a calendar report, or an error - never a short answer."""
    return await fetch_report(key, extra_filter=period_filter(period), max_pages=40, require_complete=True)

def default_window(key: str, today: date) -> tuple[date, date]:
    """The capture window each daily archive run uses."""
    if key == "restricted_release":
        return today - timedelta(days=1), today + timedelta(days=45)
    if key == "ipo_calendar":
        return today - timedelta(days=3), today + timedelta(days=21)
    if key in {"block_trade", "margin_detail", "margin_market"}:
        return today - timedelta(days=3), today
    return today - timedelta(days=2), today + timedelta(days=1)


__all__ = [
    "PROVIDER_KEY", "REPORTS", "ReportSpec", "available_at", "default_window", "fetch_report",
    "period_filter", "period_report", "report_events", "report_observations", "row_symbol", "suspension_filter",
]
