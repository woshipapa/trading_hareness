"""Bounded savepoint retry for owner ``quant.instruments`` writes.

The peer reaches PostgreSQL through a tunnel and several close/backfill
workers can discover the same symbol set at once.  PostgreSQL may report a
lock timeout (55P03) or deadlock (40P01) while the transaction is otherwise
owned by the caller.  This helper retries only that narrow class, rolls back
to its own savepoint, and never retries an arbitrary constraint or network
failure.
"""

from __future__ import annotations

import time
from typing import Any, Callable, TypeVar


LOCK_TIMEOUT_MS = 300
TOTAL_RETRY_SECONDS = 60.0
RETRYABLE_SQLSTATES = frozenset({"55P03", "40P01"})
_T = TypeVar("_T")


def _execute(connection: Any, statement: str, parameters: Any = None) -> Any:
    if hasattr(connection, "execute"):
        return connection.execute(statement, parameters)
    with connection.cursor() as cursor:
        return cursor.execute(statement, parameters)


def _sqlstate(error: BaseException) -> str | None:
    value = getattr(error, "sqlstate", None) or getattr(error, "pgcode", None)
    return str(value).upper() if value else None


def is_retryable_instrument_error(error: BaseException) -> bool:
    """Return true only for PostgreSQL lock timeout/deadlock SQLSTATEs."""
    return _sqlstate(error) in RETRYABLE_SQLSTATES


def run_instrument_write_with_retry(
    connection: Any,
    operation: Callable[[], _T],
    *,
    lock_timeout_ms: int = LOCK_TIMEOUT_MS,
    total_timeout_seconds: float = TOTAL_RETRY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> _T:
    """Run one instrument statement inside a savepoint and bounded retries.

    The caller must already own the surrounding transaction.  ``SET LOCAL``
    is deliberately scoped to this savepoint/transaction; no session-wide
    timeout is changed.  The injectable clock/sleeper keeps unit tests fast
    and makes the retry budget explicit.
    """
    if lock_timeout_ms <= 0 or total_timeout_seconds <= 0:
        raise ValueError("lock timeout and retry budget must be positive")
    # A few repository contract fakes intentionally expose only
    # ``executemany``.  They cannot represent a savepoint; preserve their
    # one-statement contract while real psycopg connections always take the
    # guarded path below.
    if not hasattr(connection, "execute"):
        probe = connection.cursor()
        if not hasattr(probe, "execute"):
            return operation()
    deadline = monotonic() + total_timeout_seconds
    attempt = 0
    while True:
        savepoint = f"instrument_write_{attempt}"
        _execute(connection, f"SAVEPOINT {savepoint}")
        try:
            _execute(connection, f"SET LOCAL lock_timeout = '{int(lock_timeout_ms)}ms'")
            result = operation()
            _execute(connection, f"RELEASE SAVEPOINT {savepoint}")
            return result
        except Exception as error:
            try:
                _execute(connection, f"ROLLBACK TO SAVEPOINT {savepoint}")
                _execute(connection, f"RELEASE SAVEPOINT {savepoint}")
            except Exception:
                # Preserve the original database error.  If rollback itself
                # fails, the caller's transaction manager must abort it.
                pass
            if not is_retryable_instrument_error(error):
                raise
            now = monotonic()
            if now >= deadline:
                raise TimeoutError(
                    f"instrument write retry budget exhausted after {attempt + 1} attempts"
                ) from error
            delay = min(0.05 * (2 ** min(attempt, 7)), max(0.0, deadline - now))
            if delay:
                sleep(delay)
            attempt += 1


__all__ = [
    "LOCK_TIMEOUT_MS", "TOTAL_RETRY_SECONDS", "RETRYABLE_SQLSTATES",
    "is_retryable_instrument_error", "run_instrument_write_with_retry",
]
