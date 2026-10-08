"""Read-only provider catalog and capability presentation services."""

from __future__ import annotations

from typing import Any


def provider_capabilities_snapshot(database: Any) -> dict[str, Any]:
    """Return the persisted cross-provider capability matrix without polling."""
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT c.provider_key,p.label,c.api_name,c.availability,c.frequency,c.decision_eligible,c.note,c.verified_at,c.last_checked_at,c.metadata
               FROM quant.provider_api_capabilities c JOIN quant.providers p ON p.provider_key=c.provider_key
               ORDER BY c.api_name,c.provider_key"""
        ).fetchall()
    return {"items": rows}
