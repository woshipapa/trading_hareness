"""Read-only projection of the owner's append-only deployment announcements.

The owner writes ``quant.owner_deploy_events`` before a release can interrupt
the HTTP or shared-tunnel surfaces.  Peer processes must treat an active
``starting`` row as an operational state, not as an application failure.  The
projection is deliberately fail-open when the table is unavailable so an
older owner release does not take the peer down; the owner contract/startup
gate remains the authoritative compatibility check.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


def _value(row: Any, key: str, index: int = 0) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    return row[index]


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        stamp = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc).isoformat()
    return str(value)


def owner_deploy_status(connection: Any) -> dict[str, Any]:
    """Return the current owner release announcement without changing state.

    A deploy is active only when its latest relevant row is ``starting`` and
    no later ``completed``/``failed`` row exists for the same deploy id.  The
    owner table is intentionally queried through ``to_regclass`` first: this
    avoids turning a pre-announcement owner release into a repeated SQL error
    loop in the peer logs.
    """
    try:
        present = connection.execute(
            "SELECT to_regclass('quant.owner_deploy_events') IS NOT NULL AS present",
        ).fetchone()
        table_exists = bool(_value(present, "present", 0)) if present else False
        if not table_exists:
            return {
                "status": "unavailable",
                "reason": "owner_deploy_events_table_missing",
                "active": False,
                "pause_writes": False,
            }
        row = connection.execute(
            """SELECT starting.event_id,starting.deploy_id,starting.phase,
                      starting.release_id,starting.surfaces,starting.expected_seconds,
                      starting.recorded_at
                 FROM quant.owner_deploy_events starting
                WHERE starting.phase='starting'
                  AND NOT EXISTS (
                        SELECT 1
                          FROM quant.owner_deploy_events terminal
                         WHERE terminal.deploy_id=starting.deploy_id
                           AND terminal.phase IN ('completed','failed')
                           AND terminal.event_id>starting.event_id
                  )
                ORDER BY starting.event_id DESC
                LIMIT 1""",
        ).fetchone()
    except Exception as error:  # noqa: BLE001 - health must remain read-only and bounded
        return {
            "status": "unavailable",
            "reason": f"owner_deploy_events_query_failed:{type(error).__name__}",
            "active": False,
            "pause_writes": False,
        }

    if not row:
        return {
            "status": "idle",
            "active": False,
            "pause_writes": False,
            "deploy_id": None,
            "phase": None,
            "release_id": None,
            "surfaces": {},
            "expected_seconds": None,
            "recorded_at": None,
        }

    surfaces = _json_object(_value(row, "surfaces", 4))
    pause_writes = bool(surfaces.get("shared_tunnel") is True)
    return {
        "status": "in_progress",
        "active": True,
        "pause_writes": pause_writes,
        "deploy_id": str(_value(row, "deploy_id", 1) or ""),
        "phase": str(_value(row, "phase", 2) or "starting"),
        "release_id": str(_value(row, "release_id", 3) or ""),
        "surfaces": surfaces,
        "expected_seconds": _value(row, "expected_seconds", 5),
        "recorded_at": _iso(_value(row, "recorded_at", 6)),
        "rule": "retry_during_owner_deploy; pause_writes_when_surfaces.shared_tunnel",
    }


__all__ = ["owner_deploy_status"]
