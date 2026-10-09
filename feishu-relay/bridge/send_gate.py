"""Rate limit, idempotency and audit trail for the bridge's outbound text sends.

The personal session is a real user account, so an unbounded sender is an
account-risk problem, not just a noise problem.  Every send attempt is one
row in ``send_audit``; the per-chat and global limits are sliding windows
counted over that same table, so they survive a bridge restart and need no
second source of truth.  A failed attempt does not consume budget.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class SendRateLimited(Exception):
	def __init__(self, scope: str, retry_after: int) -> None:
		super().__init__(f"发送超限({scope}),请 {retry_after}s 后重试")
		self.scope = scope
		self.retry_after = retry_after


class SendConflict(Exception):
	"""The idempotency key is already used by a different or in-flight request."""


class SendGate:
	def __init__(
		self,
		path: str | os.PathLike[str],
		*,
		per_chat_per_min: int = 6,
		global_per_hour: int = 120,
		idempotency_ttl_seconds: int = 24 * 3600,
	) -> None:
		self.path = Path(path).expanduser()
		self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
		self.per_chat_per_min = max(1, int(per_chat_per_min))
		self.global_per_hour = max(1, int(global_per_hour))
		self.idempotency_ttl_seconds = max(60, int(idempotency_ttl_seconds))
		self._lock = threading.RLock()
		self._db = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
		self._db.row_factory = sqlite3.Row
		self._db.execute("PRAGMA journal_mode=WAL")
		self._db.execute("PRAGMA busy_timeout=30000")
		self._db.executescript(
			"""
			CREATE TABLE IF NOT EXISTS send_audit (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				idempotency_key TEXT,
				chat_id TEXT NOT NULL,
				text_sha256 TEXT NOT NULL,
				text_length INTEGER NOT NULL,
				root_id TEXT,
				result TEXT NOT NULL,
				error TEXT,
				created_at REAL NOT NULL,
				finished_at REAL
			);
			CREATE INDEX IF NOT EXISTS send_audit_key ON send_audit(idempotency_key, created_at);
			CREATE INDEX IF NOT EXISTS send_audit_chat ON send_audit(chat_id, created_at);
			CREATE INDEX IF NOT EXISTS send_audit_time ON send_audit(created_at);
			"""
		)
		self._db.commit()
		# A crash between begin() and finish() leaves a 'pending' row that would
		# block its key and eat budget forever; the outcome is unknown, say so.
		self._db.execute(
			"UPDATE send_audit SET result='unknown', error='bridge restarted mid-send', finished_at=? WHERE result='pending'",
			(time.time(),),
		)
		self._db.commit()

	@staticmethod
	def digest(text: str) -> str:
		return hashlib.sha256(text.encode("utf-8")).hexdigest()

	def _count_locked(self, since: float, chat_id: str | None = None) -> tuple[int, float | None]:
		sql = "SELECT COUNT(*) AS n, MIN(created_at) AS oldest FROM send_audit WHERE created_at>=? AND result IN ('pending','accepted','unknown')"
		args: list[Any] = [since]
		if chat_id is not None:
			sql += " AND chat_id=?"
			args.append(chat_id)
		row = self._db.execute(sql, args).fetchone()
		return int(row["n"]), row["oldest"]

	def begin(self, *, chat_id: str, text: str, root_id: str | None, idempotency_key: str | None) -> dict[str, Any]:
		"""Admit one send.  Returns ``{"replay": row}`` for a repeated key, else ``{"audit_id": n}``."""
		now = time.time()
		text_hash = self.digest(text)
		with self._lock:
			if idempotency_key:
				prior = self._db.execute(
					"SELECT * FROM send_audit WHERE idempotency_key=? AND created_at>=? ORDER BY id DESC LIMIT 1",
					(idempotency_key, now - self.idempotency_ttl_seconds),
				).fetchone()
				if prior is not None:
					if prior["chat_id"] != chat_id or prior["text_sha256"] != text_hash or (prior["root_id"] or None) != root_id:
						raise SendConflict("idempotency_key 已被另一份内容使用")
					if prior["result"] in ("accepted", "unknown"):
						return {"replay": dict(prior)}
					if prior["result"] == "pending":
						raise SendConflict("相同 idempotency_key 的发送仍在进行")
					# A failed first attempt may be retried under the same key.
			chat_count, chat_oldest = self._count_locked(now - 60, chat_id)
			if chat_count >= self.per_chat_per_min:
				raise SendRateLimited("chat", max(1, int((chat_oldest or now) + 60 - now) + 1))
			hour_count, hour_oldest = self._count_locked(now - 3600)
			if hour_count >= self.global_per_hour:
				raise SendRateLimited("global", max(1, int((hour_oldest or now) + 3600 - now) + 1))
			cursor = self._db.execute(
				"INSERT INTO send_audit(idempotency_key, chat_id, text_sha256, text_length, root_id, result, created_at) VALUES (?,?,?,?,?,'pending',?)",
				(idempotency_key or None, chat_id, text_hash, len(text), root_id, now),
			)
			self._db.commit()
			return {"audit_id": int(cursor.lastrowid)}

	def finish(self, audit_id: int, *, ok: bool, error: str | None = None) -> None:
		with self._lock:
			self._db.execute(
				"UPDATE send_audit SET result=?, error=?, finished_at=? WHERE id=?",
				("accepted" if ok else "failed", None if ok else (error or "")[:240], time.time(), audit_id),
			)
			self._db.commit()

	def audit(self, limit: int = 50, chat_id: str | None = None) -> list[dict[str, Any]]:
		limit = max(1, min(int(limit), 500))
		sql = "SELECT id, idempotency_key, chat_id, text_sha256, text_length, root_id, result, error, created_at, finished_at FROM send_audit"
		args: list[Any] = []
		if chat_id:
			sql += " WHERE chat_id=?"
			args.append(chat_id)
		sql += " ORDER BY id DESC LIMIT ?"
		args.append(limit)
		with self._lock:
			return [dict(row) for row in self._db.execute(sql, args).fetchall()]

	def stats(self, chat_ids: list[str] | None = None) -> dict[str, Any]:
		now = time.time()
		with self._lock:
			hour_count, _ = self._count_locked(now - 3600)
			remaining = {}
			for chat_id in chat_ids or []:
				used, _oldest = self._count_locked(now - 60, chat_id)
				remaining[chat_id] = max(0, self.per_chat_per_min - used)
			failure = self._db.execute(
				"SELECT id, chat_id, error, created_at FROM send_audit WHERE result='failed' ORDER BY id DESC LIMIT 1"
			).fetchone()
			failed_1h = self._db.execute("SELECT COUNT(*) AS n FROM send_audit WHERE result='failed' AND created_at>=?", (now - 3600,)).fetchone()["n"]
		return {
			"per_chat_per_min": self.per_chat_per_min,
			"global_per_hour": self.global_per_hour,
			"global_remaining": max(0, self.global_per_hour - hour_count),
			"chat_remaining": remaining,
			"failed_last_hour": int(failed_1h),
			"last_failure": dict(failure) if failure else None,
		}

	def close(self) -> None:
		with self._lock:
			self._db.close()
