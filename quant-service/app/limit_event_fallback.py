"""Project persisted limit-pool events into the limit-pool rows research reads.

The licensed ``limit_list_ths`` and ``limit_step`` pools stopped on
2026-10-08 (decision 0005).  The same THS pools still arrive through Fuyao:
``market_event_capture`` stores the limit-up pool every minute and each
ladder rung once per day in ``quant.market_events``.  These helpers turn those
events into the row shape the pattern research was written against.  The
module name predates that switch: the events are the primary evidence now,
and every projected row keeps its provider in ``provider_key``.

A Fuyao pool item (as seen in stored rows of 2026-09-01) carries name, price,
change, the THS streak label (``首板``/``N连板``), the current and the maximum
seal amount and the limit-up reason.  It has no turnover, open count, free float,
一字板 label or yearly seal rate, so those fields stay ``None`` instead of
being estimated, and it is never presented as an exchange-official pool.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from .post_close_limit_features import board_count

#: ``ladder_sources`` labels: a rung taken from the pool row's own streak label,
#: and a rung taken from the ladder capability (``limit_chain`` events).
POOL_TAG_LADDER_SOURCE = "limit_up_pool_tag"
CHAIN_LADDER_SOURCE = "limit_chain"
#: Pool fields another source of the same day may state when the Fuyao row cannot.
ENRICHABLE_FIELDS = ("turnover_rate", "open_num")


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


def _first_number(body: dict[str, Any], *keys: str) -> float | None:
    """The first present value; a real zero is kept, unlike an ``or`` chain."""
    for key in keys:
        value = _number(body.get(key))
        if value is not None:
            return value
    return None


def event_board_count(body: dict[str, Any]) -> int | None:
    """Consecutive limit-up days an event states, if it states one."""
    value = _first_number(body, "board_num", "continue_day_cnt", "连板数")
    return int(value) if value is not None and value >= 1 else None


def event_limit_record(record: dict[str, Any], *, trade_date: date, board_num: int | None = None) -> dict[str, Any]:
    """Convert a persisted ``limit_up_pool`` row to a limit-pool row.

    ``tag`` is the THS streak label the pool states (``首板``/``N连板``) when
    present, else one built from ``board_num`` (the ladder rung) or the
    pool's own count.  ``limit_amount`` is the seal amount of this snapshot,
    so it is the close seal only when the caller passes the close snapshot.
    """
    body = event_body(record)
    symbol = str(record.get("symbol") or body.get("thscode") or "").upper()
    label = str(body.get("continue_day_text") or "").strip()
    boards = max(1, int(board_num or event_board_count(body) or 1))
    return {
        "row_data": {
            "ts_code": symbol, "name": body.get("name") or body.get("名称"),
            "trade_date": trade_date.strftime("%Y%m%d"), "limit_type": "涨停池",
            "status": "涨停" if body.get("sealed", True) else "炸板",
            "price": _first_number(body, "last_price", "price", "最新价"),
            "pct_chg": _first_number(body, "price_change_ratio_pct", "pct_change", "涨跌幅"),
            "limit_amount": _first_number(body, "seal_money", "seal_amount", "封板资金"),
            "max_seal_money": _number(body.get("max_seal_money")),
            "turnover_rate": _first_number(body, "turnover_ratio_pct", "turnover_rate", "换手率"),
            "open_num": _first_number(body, "open_times", "炸板次数"),
            "tag": label if board_count(label) else "首板" if boards <= 1 else f"{boards}天{boards}板",
            "lu_desc": body.get("limit_up_reason"), "limit_up_time": body.get("limit_up_time"),
            "event_type": "limit_up_pool", "observed_at": record.get("occurred_at"),
        },
        "provider_key": f"market_events:{record.get('source') or 'unknown'}",
        "available_at": record.get("available_at"),
    }


def event_step_record(record: dict[str, Any], *, trade_date: date) -> dict[str, Any]:
    """Convert a persisted ``limit_chain`` row to a ladder (``limit_step``-like) row."""
    body = event_body(record)
    symbol = str(record.get("symbol") or body.get("thscode") or "").upper()
    rung = max(1, event_board_count(body) or 1)
    return {
        "row_data": {
            "ts_code": symbol, "name": body.get("name") or body.get("名称"),
            "trade_date": trade_date.strftime("%Y%m%d"), "nums": rung,
            "tag": "首板" if rung <= 1 else f"{rung}天{rung}板",
            "event_type": "limit_chain",
        },
        "provider_key": f"market_events:{record.get('source') or 'unknown'}",
        "available_at": record.get("available_at"),
    }


def close_pool_records(pool_rows: list[dict[str, Any]], chain_rows: list[dict[str, Any]], *,
                       trade_date: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Pool rows and multi-board ladder rows for one close snapshot.

    A ladder event is kept once per symbol and day, also after the name breaks
    its board, so only rungs of names still in the close pool belong to the
    close ladder -- what ``limit_step`` listed after the close.
    """
    rungs = {item["row_data"]["ts_code"]: item
             for item in (event_step_record(dict(row), trade_date=trade_date) for row in chain_rows)}
    pool = []
    for row in pool_rows:
        rung = rungs.get(str(row.get("symbol") or "").upper())
        pool.append(event_limit_record(dict(row), trade_date=trade_date,
                                       board_num=rung["row_data"]["nums"] if rung else None))
    members = {item["row_data"]["ts_code"] for item in pool}
    ladder = [item for symbol, item in sorted(rungs.items())
              if symbol in members and int(item["row_data"]["nums"]) >= 2]
    return pool, ladder


def enrich_pool_records(pool: list[dict[str, Any]], others: list[dict[str, Any]], *,
                        trade_date: date) -> list[dict[str, Any]]:
    """Fill fields a Fuyao pool row lacks from another source's row of the same day.

    Only absent fields are filled and the row says from where: the settled
    close-at-limit row carries the day's turnover, which the pool does not.
    Membership never changes -- another source cannot add a positive.
    """
    stated = {item["row_data"]["ts_code"]: item
              for item in (event_limit_record(dict(row), trade_date=trade_date) for row in others)}
    for item in pool:
        other = stated.get(item["row_data"]["ts_code"])
        filled = [key for key in ENRICHABLE_FIELDS
                  if other and item["row_data"].get(key) is None and other["row_data"].get(key) is not None]
        if filled:
            item["row_data"].update({key: other["row_data"][key] for key in filled})
            item["row_data"]["enriched_from"] = {"provider_key": other["provider_key"], "fields": filled}
    return pool


__all__ = [
    "CHAIN_LADDER_SOURCE", "ENRICHABLE_FIELDS", "POOL_TAG_LADDER_SOURCE", "close_pool_records", "enrich_pool_records",
    "event_board_count", "event_body", "event_limit_record", "event_step_record",
]
