"""Process-level owner lock for the single private WebSocket session."""

from __future__ import annotations

import os
import socket
import re
from pathlib import Path
from typing import TextIO

try:
    import fcntl
except ImportError:  # pragma: no cover - the supported macOS/Linux hosts have fcntl
    fcntl = None

PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def profile_storage_paths(home: str | os.PathLike[str], profile: str) -> tuple[Path, Path, Path]:
    """Return credential, spool and owner-lock paths for an isolated profile."""
    name = str(profile).strip() or "default"
    if not PROFILE_RE.fullmatch(name):
        raise ValueError("LARKX_PROFILE must contain only letters, digits, '.', '_' or '-'")
    root = Path(home).expanduser()
    profile_root = root if name == "default" else root / "profiles" / name
    return profile_root / "credentials.json", profile_root / "bridge-events.sqlite3", profile_root / "bridge.owner.lock"


class OwnerLock:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path).expanduser()
        self._handle: TextIO | None = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> None:
        if fcntl is None:
            raise RuntimeError("LarkAgentX owner lock requires a Unix host")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as error:
            handle.close()
            raise RuntimeError(f"another LarkAgentX bridge owns the WebSocket: {self.path}") from error
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} host={socket.gethostname()}\n")
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "OwnerLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
