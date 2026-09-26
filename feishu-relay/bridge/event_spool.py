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
from datetime import datetime, timezone
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
            CREATE TABLE IF NOT EXISTS chat_stats (
                chat_id TEXT PRIMARY KEY,
                observed_count INTEGER NOT NULL DEFAULT 0,
                forwarded_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                failure_count INTEGER NOT NULL DEFAULT 0,
                filtered_count INTEGER NOT NULL DEFAULT 0,
                last_observed_at REAL,
                last_forwarded_at REAL,
                last_message_type TEXT
            );
            CREATE TABLE IF NOT EXISTS runtime_counters (
                counter_name TEXT PRIMARY KEY,
                counter_value INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ignored_chat_stats (
                chat_id TEXT PRIMARY KEY,
                ignored_count INTEGER NOT NULL DEFAULT 0,
                last_position INTEGER,
                last_message_id TEXT,
                last_message_type TEXT,
                last_reason TEXT,
                first_ignored_at REAL NOT NULL,
                last_ignored_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS position_stats (
                chat_id TEXT PRIMARY KEY,
                last_position INTEGER,
                missing_position_count INTEGER NOT NULL DEFAULT 0,
                gap_event_count INTEGER NOT NULL DEFAULT 0,
                last_gap_start INTEGER,
                last_gap_end INTEGER,
                last_gap_at REAL,
                out_of_order_count INTEGER NOT NULL DEFAULT 0,
                recovered_position_count INTEGER NOT NULL DEFAULT 0,
                last_recovered_position INTEGER,
                last_recovered_at REAL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS position_recoveries (
                chat_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                recovered_at REAL NOT NULL,
                PRIMARY KEY(chat_id, position)
            );
            CREATE TABLE IF NOT EXISTS filtered_events (
                event_id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                message_id TEXT,
                position INTEGER,
                source_key TEXT,
                keyword TEXT,
                reason TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS filtered_events_chat_position_idx ON filtered_events(chat_id, position);
            CREATE TABLE IF NOT EXISTS position_coverage (
                chat_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                coverage_type TEXT NOT NULL CHECK(coverage_type IN ('observed','recovered','filtered')),
                recorded_at REAL NOT NULL,
                PRIMARY KEY(chat_id, position, coverage_type)
            );
            CREATE INDEX IF NOT EXISTS position_coverage_chat_position_idx ON position_coverage(chat_id, position);
            """
        )
        # The bridge was initially released without a sequence column. Keep the
        # upgrade merge-only so an existing local spool is never discarded.
        columns = {str(row[1]) for row in self._db.execute("PRAGMA table_info(events)").fetchall()}
        if "sequence" not in columns:
            self._db.execute("ALTER TABLE events ADD COLUMN sequence INTEGER")
            self._db.execute("CREATE UNIQUE INDEX IF NOT EXISTS events_sequence_idx ON events(sequence)")
        chat_stat_columns = {str(row[1]) for row in self._db.execute("PRAGMA table_info(chat_stats)").fetchall()}
        added_failure_count = "failure_count" not in chat_stat_columns
        if added_failure_count:
            self._db.execute("ALTER TABLE chat_stats ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0")
            self._db.execute("UPDATE chat_stats SET failure_count=failed_count WHERE failure_count=0")
        if "filtered_count" not in chat_stat_columns:
            self._db.execute("ALTER TABLE chat_stats ADD COLUMN filtered_count INTEGER NOT NULL DEFAULT 0")
        position_stat_columns = {str(row[1]) for row in self._db.execute("PRAGMA table_info(position_stats)").fetchall()}
        if "recovered_position_count" not in position_stat_columns:
            self._db.execute("ALTER TABLE position_stats ADD COLUMN recovered_position_count INTEGER NOT NULL DEFAULT 0")
        if "last_recovered_position" not in position_stat_columns:
            self._db.execute("ALTER TABLE position_stats ADD COLUMN last_recovered_position INTEGER")
        if "last_recovered_at" not in position_stat_columns:
            self._db.execute("ALTER TABLE position_stats ADD COLUMN last_recovered_at REAL")
        for row in self._db.execute("SELECT rowid FROM events WHERE sequence IS NULL ORDER BY rowid").fetchall():
            sequence = self._db.execute("SELECT COALESCE(MAX(sequence),0)+1 AS next_sequence FROM events").fetchone()["next_sequence"]
            self._db.execute("UPDATE events SET sequence=? WHERE rowid=?", (int(sequence), int(row[0])))
        self._db.execute("INSERT INTO spool_cursor(cursor_name,sequence,updated_at) VALUES('drain',0,?) ON CONFLICT(cursor_name) DO NOTHING", (self._now(),))
        self._backfill_chat_stats_locked()
        self._rebuild_position_stats_locked()
        self._backfill_position_coverage_locked()
        self._db.commit()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _now() -> float:
        return time.time()

    @staticmethod
    def _iso(value: float | None) -> str | None:
        if value is None:
            return None
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat()

    @staticmethod
    def _payload_meta(payload_json: str) -> tuple[str, str]:
        try:
            payload = json.loads(payload_json)
        except (TypeError, json.JSONDecodeError):
            return "", "UNKNOWN"
        if not isinstance(payload, dict):
            return "", "UNKNOWN"
        chat_id = str(payload.get("chat_id") or "").strip()
        message_type = str(payload.get("msg_type_name") or payload.get("msg_type") or "UNKNOWN").strip().upper()
        return chat_id, message_type or "UNKNOWN"

    @staticmethod
    def _payload_position(payload: dict[str, Any]) -> int | None:
        value = payload.get("position")
        try:
            position = int(value)
        except (TypeError, ValueError):
            return None
        return position if position > 0 else None

    def _backfill_chat_stats_locked(self) -> None:
        """Build the durable projection once for spools created before chat_stats."""
        existing = self._db.execute("SELECT count(*) AS count FROM chat_stats").fetchone()
        if existing and int(existing["count"]) > 0:
            return
        rows = self._db.execute("SELECT payload_json,status,created_at,delivered_at FROM events ORDER BY sequence").fetchall()
        aggregates: dict[str, dict[str, Any]] = {}
        for row in rows:
            chat_id, message_type = self._payload_meta(row["payload_json"])
            if not chat_id:
                continue
            item = aggregates.setdefault(chat_id, {
                "observed_count": 0, "forwarded_count": 0, "failed_count": 0, "failure_count": 0, "filtered_count": 0,
                "last_observed_at": None, "last_forwarded_at": None, "last_message_type": None,
            })
            item["observed_count"] += 1
            item["last_observed_at"] = float(row["created_at"])
            item["last_message_type"] = message_type
            if str(row["status"]) == "delivered":
                item["forwarded_count"] += 1
                item["last_forwarded_at"] = float(row["delivered_at"] or row["created_at"])
            elif str(row["status"]) == "failed":
                item["failed_count"] += 1
                item["failure_count"] += 1
        for chat_id, item in aggregates.items():
            self._db.execute(
                "INSERT INTO chat_stats(chat_id,observed_count,forwarded_count,failed_count,failure_count,filtered_count,last_observed_at,last_forwarded_at,last_message_type) VALUES(?,?,?,?,?,?,?,?,?)",
                (chat_id, item["observed_count"], item["forwarded_count"], item["failed_count"], item["failure_count"], item["filtered_count"], item["last_observed_at"], item["last_forwarded_at"], item["last_message_type"]),
            )

    def _record_observed_locked(self, payload: dict[str, Any], created_at: float) -> None:
        chat_id = str(payload.get("chat_id") or "").strip()
        if not chat_id:
            return
        message_type = str(payload.get("msg_type_name") or payload.get("msg_type") or "UNKNOWN").strip().upper() or "UNKNOWN"
        self._db.execute(
            """
            INSERT INTO chat_stats(chat_id,observed_count,last_observed_at,last_message_type)
            VALUES(?,?,?,?)
            ON CONFLICT(chat_id) DO UPDATE SET
              observed_count=chat_stats.observed_count+1,
              last_observed_at=excluded.last_observed_at,
              last_message_type=excluded.last_message_type
            """,
            (chat_id, 1, created_at, message_type),
        )

    def increment_counter(self, name: str, amount: int = 1) -> int:
        name = str(name or "").strip()
        if not name:
            raise ValueError("counter name is empty")
        now = self._now()
        with self._lock:
            self._increment_counter_locked(name, amount, now)
            self._db.commit()
            row = self._db.execute("SELECT counter_value FROM runtime_counters WHERE counter_name=?", (name,)).fetchone()
            return int(row["counter_value"] if row else 0)

    def _increment_counter_locked(self, name: str, amount: int, now: float) -> None:
        self._db.execute(
            """
            INSERT INTO runtime_counters(counter_name,counter_value,updated_at)
            VALUES(?,?,?)
            ON CONFLICT(counter_name) DO UPDATE SET
              counter_value=runtime_counters.counter_value+excluded.counter_value,
              updated_at=excluded.updated_at
            """,
            (name, int(amount), now),
        )

    def _rebuild_position_stats_locked(self) -> None:
        """Seed position telemetry from events retained before this schema existed."""
        existing = self._db.execute("SELECT count(*) AS count FROM position_stats").fetchone()
        if existing and int(existing["count"]) > 0:
            return
        states: dict[str, dict[str, Any]] = {}
        rows = self._db.execute("SELECT payload_json,created_at FROM events ORDER BY sequence").fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            chat_id = str(payload.get("chat_id") or "").strip()
            position = self._payload_position(payload)
            if not chat_id or position is None:
                continue
            state = states.setdefault(chat_id, {
                "last_position": None, "missing_position_count": 0, "gap_event_count": 0,
                "last_gap_start": None, "last_gap_end": None, "last_gap_at": None,
                "out_of_order_count": 0, "recovered_position_count": 0,
                "last_recovered_position": None, "last_recovered_at": None,
                "updated_at": float(row["created_at"]),
            })
            previous = state["last_position"]
            state["updated_at"] = float(row["created_at"])
            if previous is None:
                state["last_position"] = position
                continue
            if position <= previous:
                if position < previous:
                    state["out_of_order_count"] += 1
                continue
            missing = position - previous - 1
            if missing > 0:
                state["missing_position_count"] += missing
                state["gap_event_count"] += 1
                state["last_gap_start"] = previous + 1
                state["last_gap_end"] = position - 1
                state["last_gap_at"] = float(row["created_at"])
            state["last_position"] = position
        for chat_id, state in states.items():
            self._db.execute(
                """
                INSERT INTO position_stats(
                    chat_id,last_position,missing_position_count,gap_event_count,
                    last_gap_start,last_gap_end,last_gap_at,out_of_order_count,
                    recovered_position_count,last_recovered_position,last_recovered_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    chat_id, state["last_position"], state["missing_position_count"], state["gap_event_count"],
                    state["last_gap_start"], state["last_gap_end"], state["last_gap_at"],
                    state["out_of_order_count"], state["recovered_position_count"],
                    state["last_recovered_position"], state["last_recovered_at"], state["updated_at"],
                ),
            )

    def _backfill_position_coverage_locked(self) -> None:
        """Index retained positions once so health checks never rescan JSON payloads."""
        existing = self._db.execute("SELECT count(*) AS count FROM position_coverage").fetchone()
        if existing and int(existing["count"]) > 0:
            return
        rows = self._db.execute("SELECT payload_json,created_at FROM events").fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            chat_id = str(payload.get("chat_id") or "").strip()
            position = self._payload_position(payload)
            if chat_id and position is not None:
                self._db.execute(
                    "INSERT OR IGNORE INTO position_coverage(chat_id,position,coverage_type,recorded_at) VALUES(?,?,?,?)",
                    (chat_id, position, "observed", float(row["created_at"])),
                )
        for table, coverage_type in (("position_recoveries", "recovered"), ("filtered_events", "filtered")):
            rows = self._db.execute(f"SELECT chat_id,position,created_at FROM {table} WHERE position IS NOT NULL").fetchall() if table == "filtered_events" else self._db.execute(f"SELECT chat_id,position,recovered_at AS created_at FROM {table}").fetchall()
            for row in rows:
                self._db.execute(
                    "INSERT OR IGNORE INTO position_coverage(chat_id,position,coverage_type,recorded_at) VALUES(?,?,?,?)",
                    (str(row["chat_id"]), int(row["position"]), coverage_type, float(row["created_at"])),
                )

    def _record_position_coverage_locked(self, chat_id: str, position: int | None, coverage_type: str, now: float) -> None:
        if not chat_id or position is None or position <= 0:
            return
        self._db.execute(
            "INSERT OR IGNORE INTO position_coverage(chat_id,position,coverage_type,recorded_at) VALUES(?,?,?,?)",
            (chat_id, int(position), coverage_type, now),
        )

    def _record_position_locked(self, chat_id: str, position: int | None, now: float) -> dict[str, int] | None:
        if not chat_id or position is None:
            return None
        row = self._db.execute("SELECT * FROM position_stats WHERE chat_id=?", (chat_id,)).fetchone()
        if row is None:
            self._db.execute(
                "INSERT INTO position_stats(chat_id,last_position,updated_at) VALUES(?,?,?)",
                (chat_id, position, now),
            )
            return None
        previous = row["last_position"]
        if previous is None:
            self._db.execute("UPDATE position_stats SET last_position=?,updated_at=? WHERE chat_id=?", (position, now, chat_id))
            return None
        previous = int(previous)
        if position <= previous:
            if position < previous:
                self._db.execute(
                    "UPDATE position_stats SET out_of_order_count=out_of_order_count+1,updated_at=? WHERE chat_id=?",
                    (now, chat_id),
                )
            return None
        missing = position - previous - 1
        if missing <= 0:
            self._db.execute("UPDATE position_stats SET last_position=?,updated_at=? WHERE chat_id=?", (position, now, chat_id))
            return None
        self._db.execute(
            """
            UPDATE position_stats SET
                last_position=?, missing_position_count=missing_position_count+?,
                gap_event_count=gap_event_count+1, last_gap_start=?, last_gap_end=?,
                last_gap_at=?, updated_at=?
            WHERE chat_id=?
            """,
            (position, missing, previous + 1, position - 1, now, now, chat_id),
        )
        return {"previous": previous, "current": position, "start": previous + 1, "end": position - 1, "missing": missing}

    def record_position(self, payload: dict[str, Any]) -> dict[str, int] | None:
        chat_id = str(payload.get("chat_id") or "").strip()
        position = self._payload_position(payload)
        now = self._now()
        with self._lock:
            gap = self._record_position_locked(chat_id, position, now)
            self._record_position_coverage_locked(chat_id, position, "observed", now)
            self._db.commit()
            return gap

    def record_recovered_position(self, chat_id: str, position: Any = None) -> bool:
        """Persist a position recovered through the private history gateway.

        The observed gap counter remains an audit of what the WebSocket skipped;
        recovered_position_count makes the unresolved portion explicit without
        rewriting that original transport signal.
        """
        chat_id = str(chat_id or "").strip()
        try:
            parsed_position = int(position)
        except (TypeError, ValueError):
            return False
        if not chat_id or parsed_position <= 0:
            return False
        now = self._now()
        with self._lock:
            inserted = self._db.execute(
                "INSERT OR IGNORE INTO position_recoveries(chat_id,position,recovered_at) VALUES(?,?,?)",
                (chat_id, parsed_position, now),
            ).rowcount
            if inserted:
                self._record_position_coverage_locked(chat_id, parsed_position, "recovered", now)
                self._db.execute(
                    """
                    INSERT INTO position_stats(
                        chat_id,last_position,recovered_position_count,last_recovered_position,last_recovered_at,updated_at
                    ) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(chat_id) DO UPDATE SET
                        recovered_position_count=position_stats.recovered_position_count+1,
                        last_recovered_position=excluded.last_recovered_position,
                        last_recovered_at=excluded.last_recovered_at,
                        updated_at=excluded.updated_at
                    """,
                    (chat_id, None, 1, parsed_position, now, now),
                )
            self._db.commit()
            return bool(inserted)

    def record_filtered(
        self,
        event_id: str,
        chat_id: str,
        *,
        message_id: str = "",
        position: Any = None,
        message_type: str = "UNKNOWN",
        source_key: str = "",
        keyword: str = "",
        reason: str = "source_keyword",
    ) -> bool:
        """Record a source-filtered event without enqueueing a downstream call."""
        event_id = str(event_id or "").strip()
        chat_id = str(chat_id or "").strip()
        if not event_id or not chat_id:
            return False
        try:
            parsed_position = int(position)
            if parsed_position <= 0:
                parsed_position = None
        except (TypeError, ValueError):
            parsed_position = None
        now = self._now()
        normalized_type = str(message_type or "UNKNOWN").strip().upper() or "UNKNOWN"
        with self._lock:
            inserted = self._db.execute(
                """
                INSERT OR IGNORE INTO filtered_events(
                    event_id,chat_id,message_id,position,source_key,keyword,reason,created_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (event_id, chat_id, str(message_id or "")[:128], parsed_position, str(source_key or "")[:128], str(keyword or "")[:128], str(reason or "source_keyword")[:160], now),
            ).rowcount
            if inserted:
                self._record_position_coverage_locked(chat_id, parsed_position, "filtered", now)
                self._db.execute(
                    """
                    INSERT INTO chat_stats(chat_id,observed_count,filtered_count,last_observed_at,last_message_type)
                    VALUES(?,?,?,?,?)
                    ON CONFLICT(chat_id) DO UPDATE SET
                        observed_count=chat_stats.observed_count+1,
                        filtered_count=chat_stats.filtered_count+1,
                        last_observed_at=excluded.last_observed_at,
                        last_message_type=excluded.last_message_type
                    """,
                    (chat_id, 1, 1, now, normalized_type),
                )
            self._db.commit()
            return bool(inserted)

    def position_gap_ranges(self, chat_id: str, limit: int = 32) -> list[dict[str, int]]:
        """Return retained, unresolved position gaps for one numeric chat.

        This is intentionally derived from the durable event and recovery rows:
        a restart does not lose the ranges that still need private backfill.
        """
        chat_id = str(chat_id or "").strip()
        bounded_limit = max(1, min(128, int(limit)))
        if not chat_id:
            return []
        with self._lock:
            rows = self._db.execute(
                "SELECT position FROM position_coverage WHERE chat_id=? GROUP BY position ORDER BY position", (chat_id,)
            ).fetchall()
        ordered = [int(row["position"]) for row in rows]
        ranges: list[dict[str, int]] = []
        for previous, current in zip(ordered, ordered[1:]):
            if current - previous <= 1:
                continue
            ranges.append({"start": previous + 1, "end": current - 1, "missing": current - previous - 1})
            if len(ranges) >= bounded_limit:
                break
        return ranges

    def record_ignored(
        self,
        chat_id: str,
        *,
        position: Any = None,
        message_id: str = "",
        message_type: str = "UNKNOWN",
        reason: str = "not_allowlisted",
    ) -> dict[str, int] | None:
        chat_id = str(chat_id or "").strip()
        if not chat_id:
            return None
        try:
            parsed_position = int(position)
            if parsed_position <= 0:
                parsed_position = None
        except (TypeError, ValueError):
            parsed_position = None
        now = self._now()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO ignored_chat_stats(
                    chat_id,ignored_count,last_position,last_message_id,last_message_type,last_reason,
                    first_ignored_at,last_ignored_at
                ) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    ignored_count=ignored_chat_stats.ignored_count+1,
                    last_position=excluded.last_position,
                    last_message_id=excluded.last_message_id,
                    last_message_type=excluded.last_message_type,
                    last_reason=excluded.last_reason,
                    last_ignored_at=excluded.last_ignored_at
                """,
                (chat_id, 1, parsed_position, str(message_id or "")[:128], str(message_type or "UNKNOWN")[:32], str(reason or "not_allowlisted")[:120], now, now),
            )
            self._increment_counter_locked("ignored_count", 1, now)
            gap = self._record_position_locked(chat_id, parsed_position, now)
            self._db.commit()
            return gap

    def ignored_stats(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT chat_id,ignored_count,last_position,last_message_id,last_message_type,last_reason,first_ignored_at,last_ignored_at FROM ignored_chat_stats ORDER BY chat_id"
            ).fetchall()
        return {
            str(row["chat_id"]): {
                "ignored_count": int(row["ignored_count"]),
                "last_position": row["last_position"],
                "last_message_id": row["last_message_id"],
                "last_message_type": row["last_message_type"],
                "last_reason": row["last_reason"],
                "first_ignored_at": self._iso(row["first_ignored_at"]),
                "last_ignored_at": self._iso(row["last_ignored_at"]),
            }
            for row in rows
        }

    def ignored_summary(self) -> dict[str, int]:
        with self._lock:
            row = self._db.execute("SELECT COALESCE(SUM(ignored_count),0) AS ignored_count FROM ignored_chat_stats").fetchone()
        return {"ignored_count": int(row["ignored_count"] if row else 0)}

    def position_stats(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT chat_id,last_position,missing_position_count,gap_event_count,last_gap_start,last_gap_end,last_gap_at,out_of_order_count,recovered_position_count,last_recovered_position,last_recovered_at,updated_at FROM position_stats ORDER BY chat_id"
            ).fetchall()
        result = {
            str(row["chat_id"]): {
                "last_position": row["last_position"],
                "missing_position_count": int(row["missing_position_count"]),
                "gap_event_count": int(row["gap_event_count"]),
                "last_gap_start": row["last_gap_start"],
                "last_gap_end": row["last_gap_end"],
                "last_gap_at": self._iso(row["last_gap_at"]),
                "out_of_order_count": int(row["out_of_order_count"]),
                "recovered_position_count": int(row["recovered_position_count"]),
                "last_recovered_position": row["last_recovered_position"],
                "last_recovered_at": self._iso(row["last_recovered_at"]),
                "unresolved_position_count": 0,
                "updated_at": self._iso(row["updated_at"]),
            }
            for row in rows
        }
        for chat_id, item in result.items():
            item["unresolved_position_count"] = sum(int(gap["missing"]) for gap in self.position_gap_ranges(chat_id))
        return result

    def position_summary(self) -> dict[str, int]:
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(SUM(missing_position_count),0) AS missing_position_count, COALESCE(SUM(gap_event_count),0) AS gap_event_count, COALESCE(SUM(out_of_order_count),0) AS out_of_order_count, COALESCE(SUM(recovered_position_count),0) AS recovered_position_count FROM position_stats"
            ).fetchone()
        result = {key: int(row[key] if row else 0) for key in ("missing_position_count", "gap_event_count", "out_of_order_count", "recovered_position_count")}
        result["unresolved_position_count"] = sum(
            int(gap["missing"])
            for chat_id in self.position_stats()
            for gap in self.position_gap_ranges(chat_id)
        )
        return result

    def counters(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute("SELECT counter_name,counter_value FROM runtime_counters").fetchall()
        return {str(row["counter_name"]): int(row["counter_value"]) for row in rows}

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
            cursor = self._db.execute(
                "INSERT INTO events(sequence,event_id,payload_json,status,available_at,created_at,updated_at) VALUES(?,?,?, 'queued',?,?,?) ON CONFLICT(event_id) DO NOTHING",
                (sequence, event_id, encoded, now, now, now),
            )
            if cursor.rowcount > 0:
                self._record_observed_locked(payload, now)
                self._record_position_coverage_locked(
                    str(payload.get("chat_id") or "").strip(),
                    self._payload_position(payload),
                    "observed",
                    now,
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
            row = self._db.execute("SELECT status,payload_json FROM events WHERE event_id=?", (event_id,)).fetchone()
            self._db.execute(
                "UPDATE events SET status='delivered',lease_until=NULL,delivered_at=?,updated_at=? WHERE event_id=?",
                (now, now, event_id),
            )
            if row is not None and str(row["status"]) != "delivered":
                chat_id, _ = self._payload_meta(row["payload_json"])
                if chat_id:
                    self._db.execute(
                        "UPDATE chat_stats SET forwarded_count=forwarded_count+1,failed_count=MAX(0,failed_count-CASE WHEN ?='failed' THEN 1 ELSE 0 END),last_forwarded_at=? WHERE chat_id=?",
                        (str(row["status"]), now, chat_id),
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
            row = self._db.execute("SELECT status,payload_json FROM events WHERE event_id=?", (event_id,)).fetchone()
            self._db.execute(
                "UPDATE events SET status='failed',lease_until=NULL,available_at=?,last_error=?,updated_at=? WHERE event_id=?",
                (now + max(1, min(3600, int(retry_after))), str(error)[:1000], now, event_id),
            )
            if row is not None and str(row["status"]) != "failed":
                chat_id, _ = self._payload_meta(row["payload_json"])
                if chat_id:
                    self._db.execute("UPDATE chat_stats SET failed_count=failed_count+1,failure_count=failure_count+1 WHERE chat_id=?", (chat_id,))
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

    def chat_stats(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT chat_id,observed_count,forwarded_count,failed_count,failure_count,filtered_count,last_observed_at,last_forwarded_at,last_message_type FROM chat_stats ORDER BY chat_id"
            ).fetchall()
        return {
            str(row["chat_id"]): {
                "allowlisted": True,
                "observed_count": int(row["observed_count"]),
                "self_message_count": 0,
                "forwarded_count": int(row["forwarded_count"]),
                "failed_count": int(row["failed_count"]),
                "historical_failed_count": int(row["failure_count"]),
                "filtered_count": int(row["filtered_count"]),
                "last_observed_at": self._iso(row["last_observed_at"]),
                "last_forwarded_at": self._iso(row["last_forwarded_at"]),
                "last_message_type": row["last_message_type"],
            }
            for row in rows
        }

    def chat_summary(self) -> dict[str, int]:
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(SUM(observed_count),0) AS observed_count, COALESCE(SUM(forwarded_count),0) AS forwarded_count, COALESCE(SUM(failed_count),0) AS failed_count, COALESCE(SUM(failure_count),0) AS historical_failed_count, COALESCE(SUM(filtered_count),0) AS filtered_count FROM chat_stats"
            ).fetchone()
        return {key: int(row[key]) for key in ("observed_count", "forwarded_count", "failed_count", "historical_failed_count", "filtered_count")}

    def close(self) -> None:
        with self._lock:
            self._db.close()
