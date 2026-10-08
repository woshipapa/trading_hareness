"""HTTP write-boundary primitives shared by the composition root and tests."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
import os
import re
import secrets
import threading
from typing import Any

from fastapi import Request

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_CALLER = re.compile(r"[a-z][a-z0-9_-]{1,40}")


def write_access_allowed(method: str, supplied_key: str | None, configured_key: str | None) -> bool:
    if method.upper() not in WRITE_METHODS:
        return True
    if not configured_key:
        return True
    return bool(supplied_key) and secrets.compare_digest(supplied_key, configured_key)


# --- named, scoped write keys ---------------------------------------------------
# Twelve callers shared one X-Quant-Write-Key, so rotating it for one meant
# changing all twelve, and any of them could write anywhere. QUANT_WRITE_API_KEYS
# names each caller and the path prefixes it may write:
#
#     relay|/api/v1/ingestion/,/api/v1/internal/raw-overflow/|<key>;teacher|/api/v1/teacher-review/|<key>
#
# The single QUANT_WRITE_API_KEY keeps working as the full-scope "legacy" caller
# until the last caller has moved; /health counts writes per caller so the
# remaining legacy users are visible.


@dataclass(frozen=True)
class WriteCredential:
    caller: str
    scopes: tuple[str, ...]
    key: str = field(repr=False)

    def covers(self, path: str) -> bool:
        return any(scope == "*" or path.startswith(scope) for scope in self.scopes)


@dataclass(frozen=True)
class WriteDecision:
    allowed: bool
    status: int
    caller: str | None
    reason: str


def write_credentials(environ: Mapping[str, str] | None = None) -> tuple[tuple[WriteCredential, ...], tuple[str, ...]]:
    """Parse the configured write keys; malformed entries are reported (without values) and ignored."""
    env = os.environ if environ is None else environ
    credentials: list[WriteCredential] = []
    problems: list[str] = []
    legacy = (env.get("QUANT_WRITE_API_KEY") or "").strip()
    if legacy:
        credentials.append(WriteCredential("legacy", ("*",), legacy))
    entries = [entry.strip() for entry in re.split(r"[;\n]", env.get("QUANT_WRITE_API_KEYS") or "") if entry.strip()]
    for index, entry in enumerate(entries):
        parts = [part.strip() for part in entry.split("|")]
        if len(parts) != 3:
            problems.append(f"QUANT_WRITE_API_KEYS entry {index} is not caller|scopes|key")
            continue
        caller, raw_scopes, key = parts
        scopes = tuple(scope.strip() for scope in raw_scopes.split(",") if scope.strip())
        if not _CALLER.fullmatch(caller) or caller == "legacy":
            problems.append(f"QUANT_WRITE_API_KEYS entry {index} has an invalid caller name")
        elif not scopes or any(scope != "*" and not scope.startswith("/api/") for scope in scopes):
            problems.append(f"QUANT_WRITE_API_KEYS entry {index} ({caller}) needs '*' or /api/ path prefixes")
        elif len(key) < 24:
            problems.append(f"QUANT_WRITE_API_KEYS entry {index} ({caller}) has a key shorter than 24 characters")
        elif any(existing.caller == caller for existing in credentials):
            problems.append(f"QUANT_WRITE_API_KEYS names caller {caller} twice")
        elif any(secrets.compare_digest(existing.key, key) for existing in credentials):
            problems.append(f"QUANT_WRITE_API_KEYS entry {index} ({caller}) reuses another caller's key")
        else:
            credentials.append(WriteCredential(caller, scopes, key))
    return tuple(credentials), tuple(problems)


def match_write_credential(supplied_key: str | None, credentials: tuple[WriteCredential, ...]) -> WriteCredential | None:
    supplied = (supplied_key or "").strip()
    match = None
    for credential in credentials:  # compare with every key rather than returning on the first hit
        if supplied and secrets.compare_digest(supplied, credential.key) and match is None:
            match = credential
    return match


def authorize_write(method: str, path: str, supplied_key: str | None,
                    credentials: tuple[WriteCredential, ...]) -> WriteDecision:
    if method.upper() not in WRITE_METHODS:
        return WriteDecision(True, 200, None, "read")
    if not credentials:
        return WriteDecision(True, 200, None, "write keys not configured")
    credential = match_write_credential(supplied_key, credentials)
    if credential is None:
        return WriteDecision(False, 401, None, "valid X-Quant-Write-Key is required for write operations")
    if not credential.covers(path):
        return WriteDecision(False, 403, credential.caller,
                             f"the write key for {credential.caller} does not cover this path")
    return WriteDecision(True, 200, credential.caller, "authorized")


class WriteCallerCounter:
    """Writes per caller since start, so the callers still on the legacy key are visible."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: Counter[str] = Counter()
        self._refused: Counter[str] = Counter()

    def allowed(self, caller: str) -> None:
        with self._lock:
            self._counts[caller] += 1

    def refused(self, status: int) -> None:
        with self._lock:
            self._refused[str(status)] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"writes_by_caller": dict(self._counts), "refused_by_status": dict(self._refused)}


WRITE_CALLERS = WriteCallerCounter()


def write_boundary_status(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Secret-free: caller names and scopes, never keys."""
    credentials, problems = write_credentials(environ)
    return {
        "callers": [{"caller": item.caller, "scopes": list(item.scopes)} for item in credentials],
        "configuration_problems": list(problems),
        **WRITE_CALLERS.snapshot(),
    }


def remote_archive_sync_bearer_allowed(request: Request) -> bool:
    """Allow only the bounded bearer-shaped remote text-sync trigger."""
    if request.method.upper() != "POST" or request.url.path != "/api/v1/remote-archive/sync":
        return False
    authorization = request.headers.get("Authorization", "").strip()
    return bool(re.fullmatch(r"Bearer\s+[A-Za-z0-9._~+/-]{24,512}", authorization, flags=re.IGNORECASE))


def licensed_stock_read_allowed(request: Request, configured_key: str | None) -> bool:
    """Treat the authenticated raw-data proxy as a read despite its POST body."""
    if request.method.upper() != "POST" or request.url.path != "/licensed/stock-api/call":
        return False
    expected = str(configured_key or "").strip()
    supplied = request.headers.get("X-Quant-Read-Key", "").strip()
    return bool(expected and supplied) and secrets.compare_digest(supplied, expected)


def raw_overflow_archive_allowed(request: Request, configured: str | tuple[WriteCredential, ...] | None) -> bool:
    """Authorize the adapter-only raw archive hand-off on every verb (reads included)."""
    path = request.url.path
    if not path.startswith("/api/v1/internal/raw-overflow/"):
        return False
    supplied = request.headers.get("X-Quant-Write-Key", "").strip()
    if isinstance(configured, tuple):
        credential = match_write_credential(supplied, configured)
        return credential is not None and credential.covers(path)
    expected = str(configured or "").strip()
    return bool(expected and supplied) and secrets.compare_digest(supplied, expected)


def security_context() -> dict[str, Any]:
    return {
        "write_methods": ["POST", "PUT", "PATCH", "DELETE"],
        "header": "X-Quant-Write-Key",
        "scoped_keys_env": "QUANT_WRITE_API_KEYS (caller|path-prefixes|key; ...)",
        "remote_sync_exception": "/api/v1/remote-archive/sync",
        "licensed_read_post_exception": "/licensed/stock-api/call",
        "raw_overflow_archive_exception": "/api/v1/internal/raw-overflow/*",
        "secret_values_exposed": False,
    }


__all__ = [
    "WRITE_CALLERS",
    "WriteCredential",
    "WriteDecision",
    "authorize_write",
    "licensed_stock_read_allowed",
    "match_write_credential",
    "raw_overflow_archive_allowed",
    "remote_archive_sync_bearer_allowed",
    "security_context",
    "write_access_allowed",
    "write_boundary_status",
    "write_credentials",
]
