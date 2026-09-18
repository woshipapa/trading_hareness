"""Tiantian Fund (天天基金) daily NAV history.

Unit/accumulated NAV and the daily growth rate for any public fund code,
including LOF/ETF and OTC funds.  QDII funds publish one to two days late;
``effective_at`` is the NAV date and ``available_at`` the capture time, so a
late NAV is never back-dated into a replay.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from ..http import number, request_json


PROVIDER_KEY = "ttfund"
UPSTREAM_SITE = "api.fund.eastmoney.com"
CN_TZ = ZoneInfo("Asia/Shanghai")
_FUND_CODE = re.compile(r"\d{6}")


def normalize_nav(fund_code: str, payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    data = (payload or {}).get("Data")
    items = data.get("LSJZList") if isinstance(data, Mapping) else None
    rows = []
    for item in items or []:
        if not isinstance(item, Mapping):
            continue
        try:
            day = date.fromisoformat(str(item.get("FSRQ") or ""))
        except ValueError:
            continue
        unit = number(item.get("DWJZ"))
        if unit is None:
            continue
        rows.append({
            "fund_code": fund_code, "nav_date": day.isoformat(), "unit_nav": unit,
            "accumulated_nav": number(item.get("LJJZ")), "daily_growth_pct": number(item.get("JZZZL")),
            "subscribe_status": str(item.get("SGZT") or "") or None, "redeem_status": str(item.get("SHZT") or "") or None,
            "dividend_note": str(item.get("FHSP") or "") or None,
        })
    return rows


async def fetch_nav_history(fund_code: str, *, page_size: int = 20) -> list[dict[str, Any]]:
    if not _FUND_CODE.fullmatch(fund_code):
        raise ValueError("fund code must be six digits")
    payload = await request_json(
        "GET", "https://api.fund.eastmoney.com/f10/lsjz",
        params={"fundCode": fund_code, "pageIndex": "1", "pageSize": str(max(1, min(page_size, 49)))},
        headers={"Referer": "https://fundf10.eastmoney.com/"},
    )
    if not isinstance(payload, Mapping):
        raise ValueError("Tiantian fund response is not an object")
    return normalize_nav(fund_code, payload)


def nav_observations(rows: list[Mapping[str, Any]], observed_at: datetime) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        effective = datetime.combine(date.fromisoformat(str(row["nav_date"])), time(15, 0), CN_TZ)
        result.append({**dict(row), "ts_code": None, "effective_at": min(effective, observed_at).isoformat(),
                       "available_at": observed_at.isoformat()})
    return result


__all__ = ["PROVIDER_KEY", "UPSTREAM_SITE", "fetch_nav_history", "nav_observations", "normalize_nav"]
