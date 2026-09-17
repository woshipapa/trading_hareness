"""Longhu logical quote basket limit, independent of scheduler capacity."""

import os
from collections.abc import Mapping

LONGHU_QUOTE_MAX_SYMBOLS = 300


def intraday_longhu_max_symbols(environ: Mapping[str, str] | None = None) -> int:
    values = os.environ if environ is None else environ
    try:
        value = int(values.get("QUANT_LONGHU_INTRADAY_MAX_SYMBOLS", "300"))
    except (TypeError, ValueError):
        value = LONGHU_QUOTE_MAX_SYMBOLS
    return max(1, min(LONGHU_QUOTE_MAX_SYMBOLS, value))
