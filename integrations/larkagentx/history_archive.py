"""Durable, redacted history archive for WebSocket-delivered messages."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable


REDACTED_KEYS = {"key_hex", "iv_hex", "cookie", "cookies", "token", "access_token", "refresh_token"}


def _redact(value: Any) -> Any:
	if isinstance(value, dict):
		return {key: "[redacted]" if key.lower() in REDACTED_KEYS else _redact(child) for key, child in value.items()}
	if isinstance(value, list):
		return [_redact(child) for child in value]
	return value


class HistoryArchive:
	"""Append-only normalized event history with idempotent event IDs."""

	def __init__(self, path: str | os.PathLike[str], *, max_payload_bytes: int = 512 * 1024) -> None:
		self.path = Path(path).expanduser()
		self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
		try:
			os.chmod(self.path.parent, 0o700)
		except OSError:
			pass
		self.max_payload_bytes = max(16 * 1024, int(max_payload_bytes))
		self._lock = threading.RLock()
		self._db = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
		self._db.row_factory = sqlite3.Row
		self._db.execute("PRAGMA journal_mode=WAL")
		self._db.execute("PRAGMA busy_timeout=30000")
		self._db.executescript(
			"""
			CREATE TABLE IF NOT EXISTS messages (
				sequence INTEGER PRIMARY KEY AUTOINCREMENT,
				event_id TEXT NOT NULL UNIQUE,
				chat_id TEXT NOT NULL,
				message_id TEXT NOT NULL,
				message_type TEXT NOT NULL,
				create_time REAL,
				received_at REAL NOT NULL,
				payload_json TEXT NOT NULL
			);
			CREATE INDEX IF NOT EXISTS history_chat_time_idx ON messages(chat_id, create_time, sequence);
			CREATE INDEX IF NOT EXISTS history_chat_sequence_idx ON messages(chat_id, sequence);
			"""
		)
		self._db.commit()
		try:
			os.chmod(self.path, 0o600)
		except OSError:
			pass

	@staticmethod
	def _number(value: Any) -> float | None:
		try:
			parsed = float(value)
		except (TypeError, ValueError):
			return None
		return parsed if parsed > 0 else None

	def append(self, event_id: str, payload: dict[str, Any], *, received_at: float | None = None) -> bool:
		event_id = str(event_id or "").strip()
		if not event_id or not isinstance(payload, dict):
			return False
		redacted = _redact(payload)
		encoded = json.dumps(redacted, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
		if len(encoded.encode("utf-8")) > self.max_payload_bytes:
			return False
		chat_id = str(payload.get("chat_id") or "").strip()
		message_id = str(payload.get("msg_id") or payload.get("message_id") or "").strip()
		if not chat_id or not message_id:
			return False
		create_time = self._number(payload.get("create_time"))
		now = float(received_at or time.time())
		message_type = str(payload.get("msg_type_name") or payload.get("msg_type") or "UNKNOWN").strip().upper()
		with self._lock:
			cursor = self._db.execute(
				"INSERT OR IGNORE INTO messages(event_id,chat_id,message_id,message_type,create_time,received_at,payload_json) VALUES(?,?,?,?,?,?,?)",
				(event_id, chat_id, message_id, message_type, create_time, now, encoded),
			)
			self._db.commit()
		return cursor.rowcount > 0

	def append_many(self, events: Iterable[tuple[str, dict[str, Any]]]) -> int:
		return sum(1 for event_id, payload in events if self.append(event_id, payload))

	def export_rows(
		self,
		chat_id: str,
		*,
		from_time: float | None = None,
		to_time: float | None = None,
		after_sequence: int = 0,
		limit: int = 10000,
	) -> list[dict[str, Any]]:
		chat_id = str(chat_id or "").strip()
		limit = max(1, min(100000, int(limit)))
		clauses = ["chat_id=?", "sequence>?"]
		params: list[Any] = [chat_id, max(0, int(after_sequence))]
		if from_time is not None:
			clauses.append("(create_time IS NULL OR create_time>=?)")
			params.append(float(from_time))
		if to_time is not None:
			clauses.append("(create_time IS NULL OR create_time<=?)")
			params.append(float(to_time))
		params.append(limit)
		with self._lock:
			rows = self._db.execute(
				f"SELECT sequence,event_id,chat_id,message_id,message_type,create_time,received_at,payload_json FROM messages WHERE {' AND '.join(clauses)} ORDER BY sequence LIMIT ?",
				params,
			).fetchall()
		result = []
		for row in rows:
			try:
				payload = json.loads(row["payload_json"])
			except json.JSONDecodeError:
				continue
			result.append({
				"sequence": int(row["sequence"]), "event_id": row["event_id"], "chat_id": row["chat_id"],
				"message_id": row["message_id"], "message_type": row["message_type"],
				"create_time": row["create_time"], "received_at": row["received_at"], "payload": payload,
			})
		return result

	def stats(self, chat_id: str | None = None) -> dict[str, int]:
		with self._lock:
			if chat_id:
				row = self._db.execute("SELECT count(*) AS count, COALESCE(MAX(sequence),0) AS sequence FROM messages WHERE chat_id=?", (str(chat_id),)).fetchone()
			else:
				row = self._db.execute("SELECT count(*) AS count, COALESCE(MAX(sequence),0) AS sequence FROM messages").fetchone()
		return {"count": int(row["count"]), "latest_sequence": int(row["sequence"])}
