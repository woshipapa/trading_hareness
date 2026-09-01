"""Normalize persisted limit-event evidence for research-only fallbacks.

The local market-event stream is a derived provider and is never presented as
an exchange-official replacement for Tushare.  These helpers keep that
provenance explicit when a catalog response is empty.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any


def event_body(record: dict[str, Any]) -> dict[str, Any]:
    body = record.get("body") or {}
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            body = {}
    return dict(body) if isinstance(body, dict) else {}


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def event_limit_record(record: dict[str, Any], *, trade_date: date, board_num: int = 1) -> dict[str, Any]:
    """Convert a persisted ``limit_up_pool`` row to a source-like row."""
    body = event_body(record)
    symbol = str(record.get("symbol") or body.get("thscode") or "").upper()
    board_num = max(1, int(board_num or 1))
    return {
        "row_data": {
            "ts_code": symbol, "name": body.get("name") or body.get("名称"),
            "trade_date": trade_date.strftime("%Y%m%d"), "limit_type": "涨停池",
            "status": "涨停" if body.get("sealed", True) else "炸板",
            "price": _number(body.get("price") or body.get("last_price") or body.get("最新价")),
            "pct_chg": _number(body.get("pct_change") or body.get("price_change_ratio_pct") or body.get("涨跌幅")),
            "limit_amount": _number(body.get("seal_amount") or body.get("封板资金")),
            "turnover_rate": _number(body.get("turnover_rate") or body.get("turnover_ratio_pct") or body.get("换手率")),
            "open_num": _number(body.get("open_times") or body.get("炸板次数")),
            "tag": "首板" if board_num <= 1 else f"{board_num}天{board_num}板",
            "event_type": "limit_up_pool", "source_fallback": True,
        },
        "provider_key": f"market_events:{record.get('source') or 'unknown'}",
        "available_at": record.get("available_at"),
    }


def event_step_record(record: dict[str, Any], *, trade_date: date) -> dict[str, Any]:
    """Convert a persisted ``limit_chain`` row to a step-like row."""
    body = event_body(record)
    symbol = str(record.get("symbol") or body.get("thscode") or "").upper()
    board_num = max(1, int(_number(body.get("board_num") or body.get("连板数")) or 1))
    return {
        "row_data": {
            "ts_code": symbol, "name": body.get("name") or body.get("名称"),
            "trade_date": trade_date.strftime("%Y%m%d"), "nums": board_num,
            "tag": "首板" if board_num <= 1 else f"{board_num}天{board_num}板",
            "event_type": "limit_chain", "source_fallback": True,
        },
        "provider_key": f"market_events:{record.get('source') or 'unknown'}",
        "available_at": record.get("available_at"),
    }


__all__ = ["event_body", "event_limit_record", "event_step_record"]
