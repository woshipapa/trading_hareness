"""Which code holds a database transaction open for too long.

A long transaction holds its row locks for its whole life. On 2026-10-08 two
of them - a startup catalog projection and a board-directory upsert, both one
round trip per row over the owner tunnel - blocked a restarting service until
its startup check gave up. Static review guessed at such sites and was mostly
wrong; this records the real ones: every transaction's duration and statement
count, grouped by the function that opened it.

A slow transaction with many statements is a round-trip storm (batch it); a
slow one with few statements was waiting on something else - a lock, or network
I/O inside the transaction - which is the more dangerous kind.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
import threading
from typing import Any

LOGGER = logging.getLogger("quant.transaction_audit")
DEFAULT_SLOW_SECONDS = 5.0


def slow_transaction_seconds() -> float:
    try:
        return max(0.1, float(os.getenv("QUANT_SLOW_TRANSACTION_SECONDS", DEFAULT_SLOW_SECONDS)))
    except ValueError:
        return DEFAULT_SLOW_SECONDS


def transaction_site(frame: Any) -> str:
    """``module:function`` of the code that entered ``with db.transaction()``."""
    if frame is None:
        return "unknown"
    return f"{frame.f_globals.get('__name__', '?')}:{frame.f_code.co_name}"


@dataclass
class _SiteStats:
    transactions: int = 0
    slow: int = 0
    max_seconds: float = 0.0
    max_statements: int = 0
    last_slow_at: str | None = None


class TransactionAudit:
    """Thread-safe per-site aggregates plus the most recent slow transactions."""

    def __init__(self, recent: int = 20) -> None:
        self._lock = threading.Lock()
        self._sites: dict[str, _SiteStats] = {}
        self._recent: deque[dict[str, Any]] = deque(maxlen=recent)

    def record(self, site: str, seconds: float, statements: int, *, slow_after: float | None = None) -> bool:
        threshold = slow_transaction_seconds() if slow_after is None else slow_after
        slow = seconds >= threshold
        with self._lock:
            stats = self._sites.setdefault(site, _SiteStats())
            stats.transactions += 1
            stats.max_seconds = max(stats.max_seconds, seconds)
            stats.max_statements = max(stats.max_statements, statements)
            if slow:
                stats.slow += 1
                stats.last_slow_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                self._recent.append({"site": site, "seconds": round(seconds, 3), "statements": statements,
                                     "at": stats.last_slow_at})
        if slow:
            LOGGER.warning("slow database transaction site=%s seconds=%.1f statements=%d", site, seconds, statements)
        return slow

    def summary(self, limit: int = 5) -> dict[str, Any]:
        """Compact enough for /health: totals and the slowest sites."""
        with self._lock:
            ranked = sorted(self._sites.items(), key=lambda item: (item[1].slow, item[1].max_seconds), reverse=True)
            return {
                "slow_threshold_seconds": slow_transaction_seconds(),
                "observed_sites": len(self._sites),
                "slow_total": sum(stats.slow for stats in self._sites.values()),
                "slowest_sites": [
                    {"site": site, "slow": stats.slow, "transactions": stats.transactions,
                     "max_seconds": round(stats.max_seconds, 3), "max_statements": stats.max_statements,
                     "last_slow_at": stats.last_slow_at}
                    for site, stats in ranked[:limit] if stats.slow
                ],
                "recent_slow": list(self._recent)[-limit:],
            }

    def reset(self) -> None:
        with self._lock:
            self._sites.clear()
            self._recent.clear()


TRANSACTION_AUDIT = TransactionAudit()

__all__ = ["TRANSACTION_AUDIT", "TransactionAudit", "slow_transaction_seconds", "transaction_site"]
