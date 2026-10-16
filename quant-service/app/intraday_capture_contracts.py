"""Immutable in-memory capture and evidence-record contracts.

These contracts are shared by provider capture, strategy evaluation and the
SSD writer.  They intentionally contain no database or filesystem behavior.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
CAPTURE_BATCH_SCHEMA = "capture-batch-v1"
EVIDENCE_RECORD_SCHEMA = "evidence-record-v1"
_STREAM_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,119}$")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _frozen_rows(rows: Any) -> tuple[dict[str, Any], ...]:
    encoded = canonical_json([dict(row) for row in rows])
    value = json.loads(encoded)
    if not isinstance(value, list):  # pragma: no cover - guarded by construction
        raise ValueError("capture rows must be a list")
    return tuple(dict(row) for row in value)


@dataclass(frozen=True)
class CaptureBatch:
    batch_id: uuid.UUID
    schema_version: str
    trade_date: date
    provider_key: str
    capability: str
    observed_at: datetime
    available_at: datetime
    sequence: int
    rows: tuple[dict[str, Any], ...]
    payload_sha256: str

    @classmethod
    def build(
        cls, *, provider_key: str, capability: str, observed_at: datetime,
        available_at: datetime | None, rows: Any, batch_id: uuid.UUID | None = None,
        sequence: int = 0,
    ) -> "CaptureBatch":
        frozen = _frozen_rows(rows)
        if sequence < 0:
            raise ValueError("capture sequence must be non-negative")
        observed = observed_at if observed_at.tzinfo else observed_at.replace(tzinfo=ZoneInfo("UTC"))
        available = available_at or observed
        available = available if available.tzinfo else available.replace(tzinfo=ZoneInfo("UTC"))
        return cls(
            batch_id=batch_id or uuid.uuid4(), schema_version=CAPTURE_BATCH_SCHEMA,
            trade_date=observed.astimezone(SHANGHAI).date(), provider_key=str(provider_key),
            capability=str(capability), observed_at=observed, available_at=available,
            sequence=sequence, rows=frozen,
            payload_sha256=hashlib.sha256(canonical_json(frozen).encode("utf-8")).hexdigest(),
        )

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def header(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "batch_id": str(self.batch_id),
            "trade_date": self.trade_date.isoformat(), "provider_key": self.provider_key,
            "capability": self.capability, "observed_at": self.observed_at.isoformat(),
            "available_at": self.available_at.isoformat(), "sequence": self.sequence,
            "row_count": self.row_count, "payload_sha256": self.payload_sha256,
        }


@dataclass(frozen=True)
class EvidenceRecord:
    target_stream: str
    batch_id: uuid.UUID
    sequence: int
    provider_key: str
    capability: str
    trade_date: date
    captured_at: datetime
    effective_at: datetime
    available_at: datetime
    payload_sha256: str
    normalized: Mapping[str, Any]
    payload: Mapping[str, Any]
    schema_version: str = EVIDENCE_RECORD_SCHEMA

    @classmethod
    def from_batch(
        cls, batch: CaptureBatch, *, target_stream: str, sequence: int,
        effective_at: datetime, normalized: Mapping[str, Any], payload: Mapping[str, Any],
    ) -> "EvidenceRecord":
        if not _STREAM_RE.fullmatch(target_stream):
            raise ValueError(f"invalid evidence target stream: {target_stream}")
        if sequence < 0:
            raise ValueError("evidence sequence must be non-negative")
        encoded = canonical_json(payload)
        return cls(
            target_stream=target_stream, batch_id=batch.batch_id, sequence=sequence,
            provider_key=batch.provider_key, capability=batch.capability, trade_date=batch.trade_date,
            captured_at=batch.observed_at, effective_at=effective_at,
            available_at=batch.available_at,
            payload_sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            normalized=dict(normalized), payload=dict(payload),
        )

    @property
    def record_id(self) -> str:
        material = f"{self.batch_id}:{self.target_stream}:{self.sequence}:{self.payload_sha256}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "record_id": self.record_id,
            "target_stream": self.target_stream, "batch_id": str(self.batch_id),
            "sequence": self.sequence, "trade_date": self.trade_date.isoformat(),
            "provider_key": self.provider_key, "capability": self.capability,
            "captured_at": self.captured_at.isoformat(), "effective_at": self.effective_at.isoformat(),
            "available_at": self.available_at.isoformat(), "payload_sha256": self.payload_sha256,
            "normalized": dict(self.normalized), "payload": dict(self.payload),
        }


__all__ = [
    "CAPTURE_BATCH_SCHEMA", "EVIDENCE_RECORD_SCHEMA", "CaptureBatch", "EvidenceRecord", "canonical_json",
]
