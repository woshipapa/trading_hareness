"""Durable, local-first event spool for the LarkAgentX bridge.

The private WebSocket is an at-most-once transport from the bridge's point of
view.  Persisting the normalized event before the HTTP hand-off gives us the
same recovery boundary as an append-only event log, without ever persisting
cookies or raw protobuf frames.  The SQLite file is deliberately local and
mode 0600; the adapter remains the source of truth for message-level delivery
idempotency.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


MAX_EVENT_BYTES = 512 * 1024


class EventSpool:
    """Small SQLite queue with leases, retries and crash recovery."""

    def __init__(self, path: str | os.PathLike[str], *, max_event_bytes: int = MAX_EVENT_BYTES) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        self.max_event_bytes = max(16 * 1024, int(max_event_bytes))
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=30000")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER UNIQUE,
                event_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('queued','processing','delivered','failed')),
                attempts INTEGER NOT NULL DEFAULT 0,
                available_at REAL NOT NULL,
                lease_until REAL,
                last_error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                delivered_at REAL
            );
            CREATE INDEX IF NOT EXISTS events_ready_idx ON events(status, available_at, created_at);
            CREATE TABLE IF NOT EXISTS spool_cursor (
                cursor_name TEXT PRIMARY KEY,
                sequence INTEGER NOT NULL DEFAULT 0,
                event_id TEXT,
                updated_at REAL NOT NULL
            );
            """
        )
        # The bridge was initially released without a sequence column. Keep the
        # upgrade merge-only so an existing local spool is never discarded.
        columns = {str(row[1]) for row in self._db.execute("PRAGMA table_info(events)").fetchall()}
        if "sequence" not in columns:
            self._db.execute("ALTER TABLE events ADD COLUMN sequence INTEGER")
            self._db.execute("CREATE UNIQUE INDEX IF NOT EXISTS events_sequence_idx ON events(sequence)")
        for row in self._db.execute("SELECT rowid FROM events WHERE sequence IS NULL ORDER BY rowid").fetchall():
            sequence = self._db.execute("SELECT COALESCE(MAX(sequence),0)+1 AS next_sequence FROM events").fetchone()["next_sequence"]
            self._db.execute("UPDATE events SET sequence=? WHERE rowid=?", (int(sequence), int(row[0])))
        self._db.execute("INSERT INTO spool_cursor(cursor_name,sequence,updated_at) VALUES('drain',0,?) ON CONFLICT(cursor_name) DO NOTHING", (self._now(),))
        self._db.commit()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _now() -> float:
        return time.time()

    def enqueue(self, event_id: str, payload: dict[str, Any]) -> str:
        event_id = str(event_id).strip()
        if not event_id or len(event_id) > 256:
            raise ValueError("event_id is empty or too long")
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if len(encoded.encode("utf-8")) > self.max_event_bytes:
            raise ValueError("event payload exceeds spool limit")
        now = self._now()
        with self._lock:
            sequence = int(self._db.execute("SELECT COALESCE(MAX(sequence),0)+1 AS next_sequence FROM events").fetchone()["next_sequence"])
            self._db.execute(
                "INSERT INTO events(sequence,event_id,payload_json,status,available_at,created_at,updated_at) VALUES(?,?,?, 'queued',?,?,?) ON CONFLICT(event_id) DO NOTHING",
                (sequence, event_id, encoded, now, now, now),
            )
            self._db.commit()
            row = self._db.execute("SELECT status FROM events WHERE event_id=?", (event_id,)).fetchone()
            return str(row["status"] if row else "queued")

    def claim(self, event_id: str, *, lease_seconds: int = 300) -> str:
        """Claim an event, reclaiming a crashed worker's expired lease."""
        now = self._now()
        lease = now + max(30, min(1800, int(lease_seconds)))
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._db.execute("SELECT status,lease_until FROM events WHERE event_id=?", (event_id,)).fetchone()
            if row is None:
                self._db.rollback()
                raise KeyError(event_id)
            status = str(row["status"])
            if status == "delivered":
                self._db.commit()
                return "delivered"
            if status == "processing" and row["lease_until"] is not None and float(row["lease_until"]) > now:
                self._db.commit()
                return "in_flight"
            self._db.execute(
                "UPDATE events SET status='processing',attempts=attempts+1,lease_until=?,updated_at=?,last_error=NULL WHERE event_id=?",
                (lease, now, event_id),
            )
            self._db.commit()
            return "claimed"

    def mark_delivered(self, event_id: str) -> None:
        now = self._now()
        with self._lock:
            self._db.execute(
                "UPDATE events SET status='delivered',lease_until=NULL,delivered_at=?,updated_at=? WHERE event_id=?",
                (now, now, event_id),
            )
            self._advance_cursor_locked(now)
            self._db.commit()

    def _advance_cursor_locked(self, now: float) -> None:
        cursor = self._db.execute("SELECT sequence FROM spool_cursor WHERE cursor_name='drain'").fetchone()
        current = int(cursor["sequence"] if cursor else 0)
        while True:
            next_row = self._db.execute("SELECT sequence,event_id,status FROM events WHERE sequence>? ORDER BY sequence LIMIT 1", (current,)).fetchone()
            if next_row is None or str(next_row["status"]) != "delivered":
                break
            current = int(next_row["sequence"])
            self._db.execute("UPDATE spool_cursor SET sequence=?,event_id=?,updated_at=? WHERE cursor_name='drain'", (current, next_row["event_id"], now))

    def mark_failed(self, event_id: str, error: str, *, retry_after: int = 30) -> None:
        now = self._now()
        with self._lock:
            self._db.execute(
                "UPDATE events SET status='failed',lease_until=NULL,available_at=?,last_error=?,updated_at=? WHERE event_id=?",
                (now + max(1, min(3600, int(retry_after))), str(error)[:1000], now, event_id),
            )
            self._db.commit()

    def due(self, limit: int = 20) -> list[dict[str, Any]]:
        now = self._now()
        bounded = max(1, min(100, int(limit)))
        with self._lock:
            rows = self._db.execute(
                "SELECT event_id,payload_json,status,attempts,last_error,created_at FROM events WHERE (status IN ('queued','failed') AND available_at<=?) OR (status='processing' AND lease_until<=?) ORDER BY created_at LIMIT ?",
                (now, now, bounded),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except json.JSONDecodeError:
                continue
            result.append({"event_id": row["event_id"], "payload": payload, "status": row["status"], "attempts": int(row["attempts"]), "last_error": row["last_error"], "created_at": row["created_at"]})
        return result

    def all_payloads(self) -> list[tuple[str, dict[str, Any]]]:
        """Return normalized events for one-time history-archive backfill."""
        with self._lock:
            rows = self._db.execute("SELECT event_id,payload_json FROM events ORDER BY sequence").fetchall()
        result: list[tuple[str, dict[str, Any]]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                result.append((str(row["event_id"]), payload))
        return result

    def stats(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute("SELECT status,count(*) AS count FROM events GROUP BY status").fetchall()
        result = {"queued": 0, "processing": 0, "delivered": 0, "failed": 0}
        for row in rows:
            result[str(row["status"])] = int(row["count"])
        result["pending"] = result["queued"] + result["processing"] + result["failed"]
        with self._lock:
            cursor = self._db.execute("SELECT sequence FROM spool_cursor WHERE cursor_name='drain'").fetchone()
            result["drain_cursor"] = int(cursor["sequence"] if cursor else 0)
        return result

    def close(self) -> None:
        with self._lock:
            self._db.close()
