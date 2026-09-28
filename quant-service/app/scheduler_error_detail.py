"""Small, dependency-free error formatting helpers for retrying schedulers."""

from __future__ import annotations

from typing import Any


def scheduler_error_detail(error: BaseException, limit: int = 300) -> str:
    """Keep scheduler failures useful even when ``str(error)`` is empty."""
    error_type = type(error).__name__
    detail = str(error).strip() or repr(error)
    return f"{error_type}: {detail}"[:max(1, int(limit))]


__all__ = ["scheduler_error_detail"]
