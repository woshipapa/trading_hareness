"""Native async local market-event projections."""

from __future__ import annotations

from datetime import date
import re
from typing import Any

from fastapi import HTTPException


async def market_announcements(async_database: Any, symbol: str | None, limit: int, offset: int) -> dict[str, Any]:
    limit, offset = max(1, min(limit, 500)), max(0, offset)
    async with async_database.transaction() as connection:
        if symbol:
            symbol = symbol.upper()
            if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", symbol):
                raise HTTPException(status_code=422, detail="symbol must use the Tushare form, for example 000636.SZ")
            result = await connection.execute(
                """SELECT event_id,symbol,event_type,occurred_at,available_at,source,title,url,created_at
                     FROM quant.market_events WHERE symbol=%s ORDER BY occurred_at DESC,created_at DESC LIMIT %s OFFSET %s""",
                (symbol, limit, offset),
            )
            rows = await result.fetchall()
            total_result = await connection.execute("SELECT count(*)::int total FROM quant.market_events WHERE symbol=%s", (symbol,))
        else:
            result = await connection.execute(
                """SELECT event_id,symbol,event_type,occurred_at,available_at,source,title,url,created_at
                     FROM quant.market_events ORDER BY occurred_at DESC,created_at DESC LIMIT %s OFFSET %s""",
                (limit, offset),
            )
            rows = await result.fetchall()
            total_result = await connection.execute("SELECT count(*)::int total FROM quant.market_events")
        total = (await total_result.fetchone())["total"]
    return {"items": rows, "limit": limit, "offset": offset, "total": total,
            "next_offset": offset + len(rows) if offset + len(rows) < total else None}


async def market_events(async_database: Any, symbol: str | None, event_type: str | None,
                        trade_date: date | None, limit: int, offset: int) -> dict[str, Any]:
    limit, offset = max(1, min(limit, 500)), max(0, offset)
    if symbol:
        symbol = symbol.upper()
        if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", symbol):
            raise HTTPException(status_code=422, detail="symbol must use the Tushare form, for example 000636.SZ")
    event_type = event_type.strip() if event_type else None
    where = ("(%s::text IS NULL OR symbol=%s) AND (%s::text IS NULL OR event_type=%s) AND "
             "(%s::date IS NULL OR (occurred_at AT TIME ZONE 'Asia/Shanghai')::date=%s)")
    params: tuple[Any, ...] = (symbol, symbol, event_type, event_type, trade_date, trade_date)
    async with async_database.transaction() as connection:
        result = await connection.execute(
            f"""SELECT event_id,symbol,event_type,occurred_at,available_at,source,title,body,url,
                              content_sha256,event_identity_key,created_at,
                              count(*) OVER()::int AS __total_count
                         FROM quant.market_events WHERE {where}
                        ORDER BY occurred_at DESC,created_at DESC LIMIT %s OFFSET %s""",
            (*params, limit, offset),
        )
        rows = await result.fetchall()
        row_dicts = [dict(row) for row in rows]
        total = int(row_dicts[0]["__total_count"]) if row_dicts and row_dicts[0].get("__total_count") is not None else None
        rows = [{key: value for key, value in row.items() if key != "__total_count"} for row in row_dicts]
        if total is None:
            # An offset beyond the end has no window row. Keep the exact total
            # contract for that uncommon page without adding a second query to
            # the normal first-page path.
            total_result = await connection.execute(f"SELECT count(*)::int total FROM quant.market_events WHERE {where}", params)
            total = int((await total_result.fetchone())["total"])
    return {"items": rows, "symbol": symbol, "event_type": event_type,
            "trade_date": str(trade_date) if trade_date else None,
            "limit": limit, "offset": offset, "total": total,
            "next_offset": offset + len(rows) if offset + len(rows) < total else None,
            "research_only": True}


async def market_lhb_events(async_database: Any, trade_date: date | None, limit: int) -> dict[str, Any]:
    async with async_database.transaction() as connection:
        result = await connection.execute(
            """SELECT event_id,symbol,event_type,occurred_at,available_at,source,title,url,created_at
                 FROM quant.market_events
                WHERE event_type='lhb_event' AND (%s::date IS NULL OR occurred_at::date=%s)
                ORDER BY occurred_at DESC,created_at DESC LIMIT %s""",
            (trade_date, trade_date, max(1, min(limit, 500))),
        )
        rows = await result.fetchall()
    return {"items": rows, "trade_date": str(trade_date) if trade_date else None,
            "notice": "龙虎榜为收盘后公开信息，仅进入下一交易日观察和复盘，不参与当天盘中评分。"}


__all__ = ["market_announcements", "market_events", "market_lhb_events"]
