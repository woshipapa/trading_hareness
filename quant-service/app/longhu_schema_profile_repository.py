"""Database read projection for value-free Longhu schema observations."""

from __future__ import annotations

from typing import Any

from .longhu_schema_profile import profile


DEFAULT_SAMPLE_LIMIT = 2000
STATEMENT_TIMEOUT_MS = 5000


def schema_profile(database: Any, limit: int = DEFAULT_SAMPLE_LIMIT) -> dict[str, Any]:
    """Return bounded field metadata from persisted evidence, never raw values."""
    bounded_limit = max(1, min(DEFAULT_SAMPLE_LIMIT, int(limit)))
    with database.transaction() as connection:
        try:
            connection.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'")
            rows = connection.execute(
                """SELECT payload->>'target' AS target,payload->>'action' AS action,
                          payload->'payload' AS payload
                     FROM quant.raw_market_observations
                    WHERE provider_key='longhuvip'
                      AND coalesce(payload->>'target','')<>''
                      AND coalesce(payload->>'action','')<>''
                    ORDER BY available_at DESC
                    LIMIT %s""",
                (bounded_limit,),
            ).fetchall()
        except Exception:
            return {
                **profile([]), "status": "blocked",
                "reason": "longhu_schema_profile_query_unavailable",
            }
    return profile(dict(row) for row in rows)


__all__ = ["DEFAULT_SAMPLE_LIMIT", "STATEMENT_TIMEOUT_MS", "schema_profile"]
