"""Durable local-SSD staging for high-frequency research evidence.

The spool is deliberately independent of PostgreSQL.  A capture writes one
validated immutable envelope to the local filesystem before returning; the
盘后 replay can safely retry because the database insert uses the existing
raw-observation idempotency key.  This module does not decide which evidence
is eligible for strategy execution.
"""

from __future__ import annotations

import hashlib
import asyncio
import json
import os
import shutil
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Mapping

from . import settings
from .intraday_evidence_adapters import REPLAY_ADAPTERS
from .intraday_evidence_streams import stream_contract


SCHEMA = "intraday-evidence-spool-v1"
RECORD_TYPE = "raw_market_observation"
SEGMENT_SCHEMA = "evidence-segment-v1"
_REQUIRED_OBSERVATION_KEYS = frozenset({
    "schema_version", "target_stream", "batch_id", "sequence", "captured_at",
    "provider_key", "capability", "market", "symbol", "effective_at", "available_at",
    "payload_sha256", "normalized", "payload",
})


class SpoolCorrupt(ValueError):
    """An envelope failed schema, digest or required-field validation."""


@dataclass(frozen=True)
class SpoolConfig:
    root: Path
    enabled: bool = False
    min_free_bytes: int = 3 * 1024**3
    memory_items: int = 2048
    memory_max_bytes: int = 256 * 1024**2
    max_batch: int = 100
    replay_interval_seconds: float = 30.0
    replay_backoff_seconds: float = 120.0
    done_retention_days: int = 7
    segment_max_bytes: int = 1 * 1024 * 1024
    segment_max_records: int = 500
    segment_max_age_seconds: float = 0.2
    replay_streams: tuple[str, ...] = ("raw_market_observations",)

    @classmethod
    def from_settings(cls) -> "SpoolConfig":
        return cls(
            root=Path(settings.text("INTRADAY_EVIDENCE_SPOOL_DIR") or "/var/lib/quant/intraday-spool"),
            enabled=settings.flag("INTRADAY_EVIDENCE_SPOOL_ENABLED"),
            min_free_bytes=settings.integer(
                "INTRADAY_EVIDENCE_SPOOL_MIN_FREE_BYTES", minimum=64 * 1024**2, maximum=2**40,
            ),
            memory_items=settings.integer("INTRADAY_EVIDENCE_SPOOL_MEMORY_ITEMS", minimum=32, maximum=100_000),
            memory_max_bytes=settings.integer(
                "INTRADAY_EVIDENCE_SPOOL_MEMORY_MAX_BYTES", minimum=1 * 1024**2, maximum=4 * 1024**3,
            ),
            max_batch=settings.integer("INTRADAY_EVIDENCE_SPOOL_MAX_BATCH", minimum=1, maximum=20_000),
            replay_interval_seconds=settings.number(
                "INTRADAY_EVIDENCE_SPOOL_REPLAY_INTERVAL_SECONDS", minimum=5.0, maximum=3600.0,
            ),
            replay_backoff_seconds=settings.number(
                "INTRADAY_EVIDENCE_SPOOL_REPLAY_BACKOFF_SECONDS", minimum=10.0, maximum=3600.0,
            ),
            done_retention_days=settings.integer(
                "INTRADAY_EVIDENCE_SPOOL_DONE_RETENTION_DAYS", minimum=1, maximum=365,
            ),
            segment_max_bytes=settings.integer(
                "INTRADAY_EVIDENCE_SPOOL_SEGMENT_MAX_BYTES", minimum=16 * 1024, maximum=128 * 1024**2,
            ),
            segment_max_records=settings.integer(
                "INTRADAY_EVIDENCE_SPOOL_SEGMENT_MAX_RECORDS", minimum=1, maximum=100_000,
            ),
            segment_max_age_seconds=settings.number(
                "INTRADAY_EVIDENCE_SPOOL_SEGMENT_MAX_AGE_SECONDS", minimum=0.01, maximum=60.0,
            ),
            replay_streams=tuple(
                item.strip() for item in settings.text("INTRADAY_EVIDENCE_SPOOL_REPLAY_STREAMS").split(",") if item.strip()
            ) or ("raw_market_observations",),
        )


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _safe_part(value: Any, fallback: str = "unknown") -> str:
    text = str(value or fallback).strip()
    cleaned = "".join(char if char.isalnum() or char in "._-" else "_" for char in text)
    return cleaned[:120] or fallback


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _trade_date(observation: Mapping[str, Any]) -> str:
    raw = observation.get("effective_at")
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    text = str(raw or "")
    return text[:10] if len(text) >= 10 else datetime.now(timezone.utc).date().isoformat()


def _normalise_observation(observation: Mapping[str, Any]) -> dict[str, Any]:
    missing = sorted(_REQUIRED_OBSERVATION_KEYS - set(observation))
    if missing:
        raise ValueError(f"spool observation missing fields: {','.join(missing)}")
    result = json.loads(_canonical(dict(observation)))
    if not isinstance(result, dict):  # pragma: no cover - guarded by input type
        raise ValueError("spool observation must be an object")
    digest = str(result.get("payload_sha256") or "").lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("spool observation payload_sha256 is invalid")
    actual_payload_digest = hashlib.sha256(_canonical(result.get("payload")).encode("utf-8")).hexdigest()
    if digest != actual_payload_digest:
        raise ValueError("spool observation payload_sha256 does not match payload")
    stream_contract(str(result.get("target_stream") or ""))
    return result


def _envelope(observation: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    normalized = _normalise_observation(observation)
    body = {"schema": SCHEMA, "record_type": RECORD_TYPE, "observation": normalized}
    digest = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
    return {**body, "sha256": digest}, digest


def _validate_envelope(value: Any) -> tuple[dict[str, Any], str]:
    if not isinstance(value, dict) or value.get("schema") != SCHEMA or value.get("record_type") != RECORD_TYPE:
        raise SpoolCorrupt("unknown evidence spool schema")
    observation = value.get("observation")
    body, digest = _envelope(observation if isinstance(observation, Mapping) else {})
    if value.get("sha256") != digest:
        raise SpoolCorrupt("evidence spool envelope hash mismatch")
    return body, digest


@dataclass
class _OpenSegment:
    path: Path
    header: dict[str, Any]
    records: int = 0
    bytes_written: int = 0
    opened_at: float = field(default_factory=time.monotonic)


@dataclass
class LocalEvidenceSpool:
    config: SpoolConfig
    _memory: OrderedDict[str, dict[str, Any]] = field(default_factory=OrderedDict, init=False, repr=False)
    _memory_bytes: int = field(default=0, init=False, repr=False)
    _memory_paths: OrderedDict[str, Path] = field(default_factory=OrderedDict, init=False, repr=False)
    _open: dict[tuple[str, str, str, str], _OpenSegment] = field(default_factory=dict, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def _pending_root(self) -> Path:
        return self.config.root / "pending"

    def _segment_key(self, observation: Mapping[str, Any]) -> tuple[str, str, str, str]:
        return (
            _trade_date(observation), _safe_part(observation.get("target_stream")),
            _safe_part(observation.get("provider_key")), _safe_part(observation.get("capability")),
        )

    def _segment_header(self, observation: Mapping[str, Any], segment_id: str) -> dict[str, Any]:
        return {
            "type": "header", "schema": SEGMENT_SCHEMA, "segment_id": segment_id,
            "target_stream": observation["target_stream"], "provider_key": observation["provider_key"],
            "capability": observation["capability"], "trade_date": _trade_date(observation),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def _record_line(self, observation: Mapping[str, Any], digest: str) -> bytes:
        return (_canonical({"type": "record", "record_id": digest, "observation": observation}) + "\n").encode("utf-8")

    def _seal(self, key: tuple[str, str, str, str], state: _OpenSegment) -> Path:
        footer_hash = hashlib.sha256()
        records: list[dict[str, Any]] = []
        try:
            lines = state.path.read_text(encoding="utf-8").splitlines()
            for line in lines[1:]:
                if line:
                    records.append(json.loads(line))
        except (OSError, json.JSONDecodeError) as error:
            raise OSError(f"cannot seal evidence segment {state.path.name}: {error}") from error
        for record in records:
            footer_hash.update((_canonical(record) + "\n").encode("utf-8"))
        footer = {
            "type": "footer", "row_count": state.records,
            "first_record_id": records[0].get("record_id") if records else None,
            "last_record_id": records[-1].get("record_id") if records else None,
            "content_sha256": footer_hash.hexdigest(),
            "closed_at": datetime.now(timezone.utc).isoformat(),
        }
        with state.path.open("ab") as handle:
            handle.write((_canonical(footer) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        destination = state.path.with_suffix("")
        os.replace(state.path, destination)
        _fsync_directory(destination.parent)
        self._open.pop(key, None)
        for digest, path in list(self._memory_paths.items()):
            if path == state.path:
                self._memory_paths[digest] = destination
        return destination

    def seal_due(self, *, force: bool = False) -> int:
        """Seal open segments whose age/size/row budget has elapsed."""
        sealed = 0
        with self._lock:
            now = time.monotonic()
            for key, state in list(self._open.items()):
                if not state.path.exists():
                    self._open.pop(key, None)
                    continue
                if force or state.bytes_written >= self.config.segment_max_bytes \
                        or state.records >= self.config.segment_max_records \
                        or now - state.opened_at >= self.config.segment_max_age_seconds:
                    self._seal(key, state)
                    sealed += 1
        return sealed

    def _discover_open_segments(self) -> None:
        """Recover append-only files created by a previous writer process."""
        root = self._pending_root()
        if not root.exists():
            return
        for path in sorted(root.rglob("*.jsonl.open")):
            if path in {state.path for state in self._open.values()}:
                continue
            try:
                lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
                header = lines[0]
                if header.get("type") != "header" or header.get("schema") != SEGMENT_SCHEMA or len(lines) < 2:
                    continue
                key = (
                    str(header.get("trade_date")), _safe_part(header.get("target_stream")),
                    _safe_part(header.get("provider_key")), _safe_part(header.get("capability")),
                )
                self._open[key] = _OpenSegment(
                    path, header, records=len(lines) - 1, bytes_written=path.stat().st_size,
                    opened_at=time.monotonic() - self.config.segment_max_age_seconds,
                )
            except (OSError, json.JSONDecodeError, AttributeError) as error:
                try:
                    self.quarantine(path, f"unrecoverable open segment: {error}")
                except OSError:
                    pass

    def write(self, observation: Mapping[str, Any]) -> str:
        """Append one validated observation to a durable JSONL segment."""
        envelope, digest = _envelope(observation)
        with self._lock:
            existing = self._memory_paths.get(digest)
            if existing is not None and existing.exists():
                return digest
            self.config.root.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(self.config.root).free
            if free < self.config.min_free_bytes:
                raise OSError(f"evidence spool disk floor reached: {free} bytes free")
            key = self._segment_key(envelope["observation"])
            state = self._open.get(key)
            if state is None:
                day, stream, provider, capability = key
                directory = self._pending_root() / day / stream
                directory.mkdir(parents=True, exist_ok=True)
                segment_id = f"{day}-{uuid.uuid4().hex}"
                path = directory / f"{segment_id}.jsonl.open"
                header = self._segment_header(envelope["observation"], segment_id)
                header_bytes = (_canonical(header) + "\n").encode("utf-8")
                with path.open("wb") as handle:
                    handle.write(header_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(path, 0o600)
                state = _OpenSegment(path, header, bytes_written=len(header_bytes))
                self._open[key] = state
            record_bytes = self._record_line(envelope["observation"], digest)
            with state.path.open("ab") as handle:
                handle.write(record_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            state.records += 1
            state.bytes_written += len(record_bytes)
            self._remember(digest, envelope)
            self._memory_paths[digest] = state.path
            self._memory_paths.move_to_end(digest)
            while len(self._memory_paths) > self.config.memory_items:
                self._memory_paths.popitem(last=False)
            if state.records >= self.config.segment_max_records or state.bytes_written >= self.config.segment_max_bytes:
                self._seal(key, state)
        return digest

    def _remember(self, digest: str, envelope: dict[str, Any]) -> None:
        encoded_size = len(_canonical(envelope).encode("utf-8"))
        previous = self._memory.pop(digest, None)
        if previous is not None:
            self._memory_bytes -= len(_canonical(previous).encode("utf-8"))
        self._memory[digest] = envelope
        self._memory_bytes += encoded_size
        self._memory.move_to_end(digest)
        while len(self._memory) > self.config.memory_items or self._memory_bytes > self.config.memory_max_bytes:
            _old_digest, old = self._memory.popitem(last=False)
            self._memory_bytes -= len(_canonical(old).encode("utf-8"))

    def pending(self, trade_date: str | None = None) -> list[Path]:
        # A reader is a durability boundary: close the current append-only
        # segment before enumerating it.  The writer still coalesces records
        # until size/age limits or until the replay/status reader arrives.
        self._discover_open_segments()
        self.seal_due(force=True)
        root = self._pending_root()
        if not root.exists():
            return []
        paths = sorted(path for path in root.rglob("*.jsonl") if path.is_file())
        if trade_date is None:
            return paths
        return [path for path in paths if trade_date in path.parts]

    def read(self, path: Path) -> tuple[dict[str, Any], str]:
        records = self.read_segment(path)
        if len(records) != 1:
            raise SpoolCorrupt("segment contains multiple records; use read_segment")
        return records[0]

    def read_segment(self, path: Path) -> list[tuple[dict[str, Any], str]]:
        try:
            lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        except (OSError, json.JSONDecodeError) as error:
            raise SpoolCorrupt(f"cannot read evidence spool file {path.name}") from error
        if len(lines) < 3 or lines[0].get("type") != "header" or lines[0].get("schema") != SEGMENT_SCHEMA:
            raise SpoolCorrupt("unknown evidence segment schema")
        footer = lines[-1]
        records = lines[1:-1]
        if footer.get("type") != "footer" or footer.get("row_count") != len(records):
            raise SpoolCorrupt("evidence segment footer does not match records")
        content_hash = hashlib.sha256()
        for record in records:
            content_hash.update((_canonical(record) + "\n").encode("utf-8"))
        if footer.get("content_sha256") != content_hash.hexdigest():
            raise SpoolCorrupt("evidence segment content hash mismatch")
        result: list[tuple[dict[str, Any], str]] = []
        for record in records:
            try:
                envelope, digest = _envelope(record.get("observation") or {})
            except ValueError as error:
                raise SpoolCorrupt(str(error)) from error
            if record.get("record_id") != digest:
                raise SpoolCorrupt("evidence segment record hash mismatch")
            result.append((envelope, digest))
        return result

    def mark_done(self, path: Path) -> None:
        relative = path.relative_to(self._pending_root())
        destination = self.config.root / "done" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(path, destination)
        _fsync_directory(destination.parent)

    def quarantine(self, path: Path, reason: str) -> Path:
        relative = path.relative_to(self._pending_root())
        destination = self.config.root / "quarantine" / relative.with_suffix(".error.json")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            payload = path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001 - preserve a bounded error even for malformed JSON
            payload = {"raw_file": path.name}
        destination.write_text(_canonical({"reason": str(reason)[:500], "envelope": payload}) + "\n", encoding="utf-8")
        os.chmod(destination, 0o600)
        path.unlink(missing_ok=True)
        _fsync_directory(destination.parent)
        return destination

    def cleanup_done(self, *, now: float | None = None) -> int:
        """Remove verified envelopes after the configured recovery window."""
        root = self.config.root / "done"
        if not root.exists():
            return 0
        cutoff = (time.time() if now is None else float(now)) - self.config.done_retention_days * 86400
        removed = 0
        for path in sorted(root.rglob("*.jsonl")):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        for directory in sorted((item for item in root.rglob("*") if item.is_dir()), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
        return removed


_SHARED_SPOOLS: dict[tuple[Any, ...], LocalEvidenceSpool] = {}
_SHARED_SPOOLS_LOCK = threading.RLock()


def shared_spool(config: SpoolConfig) -> LocalEvidenceSpool:
    """Return the process-local writer for one SSD/configuration tuple."""
    key = (
        str(config.root), config.min_free_bytes, config.memory_items, config.memory_max_bytes,
        config.segment_max_bytes, config.segment_max_records, config.segment_max_age_seconds,
        config.replay_streams,
    )
    with _SHARED_SPOOLS_LOCK:
        spool = _SHARED_SPOOLS.get(key)
        if spool is None:
            spool = LocalEvidenceSpool(config)
            _SHARED_SPOOLS[key] = spool
        return spool


def replay_pending(database: Any, *, config: SpoolConfig | None = None,
                   trade_date: str | None = None, limit: int | None = None) -> dict[str, Any]:
    """Insert valid pending envelopes in one bounded transaction and archive them.

    A move failure is intentionally reported after commit: replaying the same
    envelope is safe because the existing database conflict key is retained.
    """
    cfg = config or SpoolConfig.from_settings()
    spool = LocalEvidenceSpool(cfg)
    paths = spool.pending(trade_date)
    paths = paths[:max(0, int(cfg.max_batch if limit is None else limit))]
    valid: list[tuple[Path, dict[str, Any]]] = []
    remainder: dict[Path, list[dict[str, Any]]] = {}
    quarantined = 0
    skipped = 0
    errors: list[str] = []
    row_limit = max(0, int(cfg.max_batch if limit is None else limit))
    for path in paths:
        try:
            records = spool.read_segment(path)
            streams = {envelope["observation"].get("target_stream") for envelope, _digest in records}
            if len(streams) != 1:
                raise SpoolCorrupt("evidence segment contains multiple target streams")
            stream = next(iter(streams))
            if stream not in cfg.replay_streams:
                skipped += 1
                continue
            if stream not in REPLAY_ADAPTERS:
                raise SpoolCorrupt(f"no replay adapter is registered for this evidence stream: {stream}")
            if row_limit <= len(valid):
                break
            remaining = row_limit - len(valid)
            selected = records[:remaining]
            valid.extend((path, envelope["observation"]) for envelope, _digest in selected)
            if len(records) > remaining:
                remainder[path] = [envelope["observation"] for envelope, _digest in records[remaining:]]
        except SpoolCorrupt as error:
            spool.quarantine(path, str(error))
            quarantined += 1
            errors.append(str(error))
    if not valid:
        return {"status": "empty", "files": 0, "rows": 0, "quarantined": quarantined,
                "skipped": skipped, "errors": errors}
    inserted_rows = 0
    with database.transaction() as connection:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for _path, item in valid:
            grouped.setdefault(str(item["target_stream"]), []).append(item)
        for stream, observations in grouped.items():
            inserted_rows += REPLAY_ADAPTERS[stream](connection, observations)
    moved = 0
    move_errors = 0
    archiveable = set(dict.fromkeys(item[0] for item in valid))
    for path, observations in remainder.items():
        try:
            for observation in observations:
                spool.write(observation)
            spool.seal_due(force=True)
        except OSError as error:
            archiveable.discard(path)
            errors.append(f"split {path.name}: {error}")
    for path in archiveable:
        try:
            spool.mark_done(path)
            moved += 1
        except OSError as error:
            move_errors += 1
            errors.append(f"archive {path.name}: {error}")
    status = "completed" if not errors else "partial"
    return {
        "status": status, "files": len({item[0] for item in valid}), "rows": inserted_rows, "moved": moved,
        "quarantined": quarantined, "skipped": skipped, "move_errors": move_errors, "errors": errors,
    }


async def run_paced_replay_loop(
    database: Any,
    run_database: Any,
    *,
    config: SpoolConfig | None = None,
    sleep: Any = asyncio.sleep,
) -> None:
    """Continuously drain small batches without creating a close-time burst.

    ``run_database`` is the application's bounded blocking executor.  The
    research scheduler supplies the 5433 batch-lane database, so this loop
    never competes with the intraday 5432 session writer.  Empty passes sleep
    at the normal cadence; transaction/provider failures use a longer bounded
    backoff and leave files on SSD for the next pass.
    """
    cfg = config or SpoolConfig.from_settings()
    passes = 0
    while True:
        try:
            result = await run_database(
                partial(replay_pending, config=cfg, limit=cfg.max_batch), database, timeout_seconds=60,
            )
            passes += 1
            if passes % 20 == 0:
                LocalEvidenceSpool(cfg).cleanup_done()
            delay = cfg.replay_interval_seconds if result.get("status") != "partial" else cfg.replay_backoff_seconds
        except asyncio.CancelledError:
            raise
        except Exception:
            delay = cfg.replay_backoff_seconds
        await sleep(delay)


__all__ = [
    "LocalEvidenceSpool", "RECORD_TYPE", "SCHEMA", "SEGMENT_SCHEMA", "SpoolConfig", "SpoolCorrupt",
    "shared_spool",
    "replay_pending", "run_paced_replay_loop",
]
