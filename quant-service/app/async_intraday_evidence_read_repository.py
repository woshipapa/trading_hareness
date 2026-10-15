"""Native-async projections for persisted intraday evidence.

These dashboard reads are deliberately local-only.  Keeping the two list
projections on the small read pool prevents a busy frontend from borrowing a
legacy blocking-executor slot while the intraday scanner is active.  Decision
cards remain on their existing synchronous compatibility path because their
projection still has a larger pure-Python dependency surface.
"""

from __future__ import annotations

from typing import Any

from .intraday_evidence_read_model import LATEST_SCAN_SNAPSHOT_SQL, _json_value


async def watchlists(async_database: Any) -> dict[str, Any]:
    """Return the persisted watchlist without contacting a market provider."""
    async with async_database.transaction() as connection:
        result = await connection.execute(
            "SELECT * FROM quant.intraday_watchlists "
            "ORDER BY enabled DESC,available_quantity DESC,updated_at DESC,symbol"
        )
        rows = [dict(row) for row in await result.fetchall()]
    return {"items": rows, "notice": "观察/持仓池用于提醒范围，不会创建或提交任何订单。"}


async def latest_scan(async_database: Any, *, limit: int = 100) -> dict[str, Any]:
    """Return one bounded, already-persisted scan and its outbox evidence."""
    bounded_limit = max(1, min(limit, 200))
    async with async_database.transaction() as connection:
        result = await connection.execute(LATEST_SCAN_SNAPSHOT_SQL, (bounded_limit, bounded_limit))
        row = await result.fetchone()
    if not row:
        return {"scan": None, "signals": [], "deliveries": [], "history": []}
    row = dict(row)
    return {
        "scan": _json_value(row.get("scan"), None),
        "signals": _json_value(row.get("signals"), []),
        "deliveries": _json_value(row.get("deliveries"), []),
        "history": _json_value(row.get("history"), []),
    }


__all__ = ["latest_scan", "watchlists"]
