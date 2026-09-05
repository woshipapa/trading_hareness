"""Stable identities for research data snapshots.

The manifest is deliberately JSON-only and independent of database drivers so
tests, export tools and the API can calculate the same digest.  A snapshot key
binds the exchange date, the availability cutoff and the exact manifest
content; a later provider correction therefore creates a new research
identity instead of silently changing an old result.
"""

from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
from typing import Any, Final


MANIFEST_VERSION: Final = "research-manifest-v2"


def canonical_json(value: Any) -> str:
    """Encode nested database values deterministically for hashing."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def manifest_digest(manifest: Any) -> str:
    """Return the SHA-256 digest of a canonical manifest representation."""

    return hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


def snapshot_key(as_of_date: date, knowledge_cutoff: datetime, digest: str) -> str:
    """Bind an exchange date, aware cutoff and manifest digest into one key."""

    if knowledge_cutoff.tzinfo is None or knowledge_cutoff.utcoffset() is None:
        raise ValueError("knowledge cutoff must be timezone-aware")
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("manifest digest must be a lowercase SHA-256 hex value")
    material = f"{MANIFEST_VERSION}:{as_of_date.isoformat()}:{knowledge_cutoff.isoformat()}:{digest}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


__all__ = ["MANIFEST_VERSION", "canonical_json", "manifest_digest", "snapshot_key"]
