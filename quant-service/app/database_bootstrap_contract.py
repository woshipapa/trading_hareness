"""Environment-only decisions for the quant database bootstrap."""

from __future__ import annotations

import os


def ingestion_ledger_required() -> bool:
    """Keep the shared Feishu ledger guard strict unless standalone opts out."""
    return os.getenv("QUANT_REQUIRE_INGESTION_LEDGER", "true").strip().lower() not in {
        "0", "false", "no", "off",
    }


__all__ = ["ingestion_ledger_required"]
