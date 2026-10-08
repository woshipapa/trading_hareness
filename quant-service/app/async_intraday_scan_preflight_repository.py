"""Native-async local evidence reads used before an intraday scan persists.

These queries intentionally read only already-recorded local evidence.  They
do not call a provider, alter scan state, or participate in the following
scan-write transaction.
"""

from __future__ import annotations

from typing import Any


async def latest_board_report(async_database: Any) -> dict[str, Any] | None:
    """Return the most recent completed board-flow receipt, if one exists."""
    async with async_database.transaction() as connection:
        result = await connection.execute(
            """SELECT observed_at,status FROM quant.intraday_board_reports
                 WHERE status='completed' ORDER BY observed_at DESC LIMIT 1"""
        )
        row = await result.fetchone()
    return dict(row) if row else None


__all__ = ["latest_board_report"]
