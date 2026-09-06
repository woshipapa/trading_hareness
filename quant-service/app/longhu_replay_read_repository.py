"""Bounded database projection for Longhu supplemental replay evidence."""

from __future__ import annotations

from typing import Any

from .longhu_replay_readiness import assess


READINESS_STATEMENT_TIMEOUT_MS = 5000


def readiness(database: Any) -> dict[str, Any]:
    """Read persisted contract coverage without calling a provider."""
    with database.transaction() as connection:
        try:
            connection.execute(f"SET LOCAL statement_timeout = '{READINESS_STATEMENT_TIMEOUT_MS}ms'")
            rows = connection.execute(
                """SELECT payload->>'exchange_date' AS exchange_date,
                       payload->>'target' AS target,
                       payload->>'action' AS action
                  FROM quant.raw_market_observations
                 WHERE provider_key='longhuvip'
                   AND coalesce(payload->>'exchange_date','') ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}$'
                   AND coalesce(payload->>'target','')<>''
                   AND coalesce(payload->>'action','')<>''
                 GROUP BY 1,2,3
                 ORDER BY 1"""
            ).fetchall()
        except Exception:
            return {
                **assess([]), "status": "blocked",
                "reason": "longhu_replay_readiness_query_unavailable",
            }
    return assess(dict(row) for row in rows)


__all__ = ["READINESS_STATEMENT_TIMEOUT_MS", "readiness"]
