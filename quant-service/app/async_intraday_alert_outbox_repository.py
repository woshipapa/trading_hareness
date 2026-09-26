"""Native-async bounded operations at the intraday Feishu outbox edge.

The delivery service retains its multi-table attempt state machine through the
bounded synchronous transaction executor.  This module only handles the two
short per-scan operations: creating a durable pending row before network I/O
and selecting a bounded due retry set.
"""

from __future__ import annotations

import uuid
from typing import Any

from .intraday_alert_delivery_service import FIRST_ATTEMPT_LEASE_SECONDS


async def create_pending(async_database: Any, signal_event_id: uuid.UUID, text: str) -> uuid.UUID:
    """Persist an outbox receipt before an alert can be sent."""
    async with async_database.transaction() as connection:
        result = await connection.execute(
            """INSERT INTO quant.intraday_alert_deliveries(
                   signal_event_id,channel,status,message_text,next_attempt_at
               ) VALUES(%s,'feishu_adapter','pending',%s,now()+make_interval(secs => %s)) RETURNING delivery_id""",
            (signal_event_id, text, FIRST_ATTEMPT_LEASE_SECONDS),
        )
        row = await result.fetchone()
    return row["delivery_id"]


async def due_deliveries(async_database: Any, max_attempts: int, limit: int) -> list[dict[str, Any]]:
    """Claim at most ten retryable, unsent deliveries so two scans cannot send one row."""
    async with async_database.transaction() as connection:
        result = await connection.execute(
            """UPDATE quant.intraday_alert_deliveries claimed
                  SET next_attempt_at=now()+make_interval(secs => %s)
                WHERE claimed.delivery_id IN (
                    SELECT d.delivery_id
                      FROM quant.intraday_alert_deliveries d
                     WHERE d.channel='feishu_adapter' AND d.status IN ('pending','failed')
                       AND d.message_text IS NOT NULL AND d.message_text<>''
                       AND d.attempt_count<%s
                       AND coalesce(d.next_attempt_at,d.created_at)<=now()
                       AND NOT EXISTS (
                           SELECT 1 FROM quant.intraday_alert_deliveries sent
                            WHERE sent.signal_event_id=d.signal_event_id AND sent.status='sent'
                       )
                     ORDER BY d.created_at LIMIT %s
                     FOR UPDATE SKIP LOCKED
                )
            RETURNING claimed.delivery_id,claimed.signal_event_id,claimed.message_text""",
            (FIRST_ATTEMPT_LEASE_SECONDS, max_attempts, max(1, min(limit, 10))),
        )
        rows = await result.fetchall()
    return [dict(row) for row in rows]


__all__ = ["create_pending", "due_deliveries"]
