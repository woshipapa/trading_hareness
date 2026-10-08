"""Supplier and transport error text that is safe to keep as evidence."""

from __future__ import annotations

import re


def safe_error_detail(value: str, limit: int = 500) -> str:
    """Keep supplier diagnostics without retaining credentials in evidence."""
    compact = value.strip().replace("\n", " ")
    # Authorization values conventionally contain a scheme plus a whitespace
    # separated credential (for example ``Bearer token``).  Handle the whole
    # header before generic key/value redaction so the credential cannot remain
    # after the scheme is replaced.
    compact = re.sub(r"(?i)\bauthorization\b\s*[:=]\s*[^,;&]+", "Authorization: <redacted>", compact)
    compact = re.sub(
        r"(?i)\b(x-api-key|authorization|api[_-]?key|access[_-]?token|token)\b\s*([:=])\s*([^\s,&;]+)",
        r"\1\2<redacted>",
        compact,
    )
    # Some gateways echo bearer headers as a separated phrase rather than a
    # key/value pair.
    compact = re.sub(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+", "Bearer <redacted>", compact)
    return compact[:limit]


__all__ = ["safe_error_detail"]
