"""An exception as text that is never empty.

``str(error)`` is empty for a timeout and for many cancellations. On
2026-10-09 that left the post-close all-A listing, the public archive's 同花顺
hot-list job and its provider health with a blank reason, so a timeout could
not be told from a rate limit or a missing publication. The exception's type
always says something.
"""

from __future__ import annotations


def error_text(error: BaseException, limit: int = 240) -> str:
    message = str(error).strip()
    text = f"{type(error).__name__}: {message}" if message else type(error).__name__
    return text[:limit]


__all__ = ["error_text"]
