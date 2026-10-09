"""The day's minute cross-sections as a research panel: Parquet after the close, a bounded read API (0009, step 3).

After the close, ``export_day`` writes the session's minute documents to
``$QUANT_DATA_DIR/minute_cross_section/YYYY/YYYY-MM-DD.parquet``. It is long
format, one row per (minute, symbol), zstd-compressed. A whole day is about
1.3 million rows and tens of MB, which a backtest or a factor study loads in
one read instead of 240 database round trips.

``panel`` answers "these symbols, these fields, this session" for research. It
reads the Parquet file when the day has one and the documents otherwise (the
session in progress). It is bounded to 50 symbols and is not for polling.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from . import minute_cross_section as mcs
from . import settings

#: Columns of the panel: the row's own value first, the vendor's raw field second.
FIELDS: dict[str, tuple[str, ...]] = {
    "price": ("price",), "pct_change": ("pct_change",), "turnover": ("turnover",), "volume": ("volume",),
    "open": ("raw.open_price",), "high": ("raw.high_price",), "low": ("raw.low_price",),
    "prev_close": ("raw.prev_price",),
}
MAX_SYMBOLS = 50


def panel_path(day: date, root: Path | None = None) -> Path:
    base = root or Path(settings.text("QUANT_DATA_DIR") or "/var/lib/quant")
    return base / "minute_cross_section" / f"{day:%Y}" / f"{day.isoformat()}.parquet"


def _value(row: dict[str, Any], paths: tuple[str, ...]) -> float | None:
    for path in paths:
        head, _, sub = path.partition(".")
        value = (row.get(head) or {}).get(sub) if sub else row.get(head)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def export_day(database: Any, day: date, *, root: Path | None = None) -> dict[str, Any]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    with database.transaction() as connection:
        documents = mcs.day_documents(connection, day)
    if not documents:
        return {"status": "missing", "day": day.isoformat(), "reason": "no minute documents for the session"}
    columns: dict[str, list[Any]] = {"minute": [], "symbol": [], **{field: [] for field in FIELDS}}
    for observed_at, document in documents:
        for row in mcs.rows_of(document):
            columns["minute"].append(observed_at)
            columns["symbol"].append(str(row.get("ts_code") or row.get("symbol") or "").upper())
            for field, paths in FIELDS.items():
                columns[field].append(_value(row, paths))
    table = pa.table({
        "minute": pa.array(columns["minute"], type=pa.timestamp("s", tz="UTC")),
        "symbol": pa.array(columns["symbol"]).dictionary_encode(),
        **{field: pa.array(columns[field], type=pa.float64()) for field in FIELDS},
    })
    path = panel_path(day, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".parquet.tmp")
    pq.write_table(table, temporary, compression="zstd")
    os.replace(temporary, path)
    return {"status": "completed", "day": day.isoformat(), "minutes": len(documents), "rows": table.num_rows,
            "bytes": path.stat().st_size, "path": str(path)}


def panel(database: Any, day: date, symbols: Sequence[str], fields: Sequence[str] | None = None, *,
          root: Path | None = None) -> dict[str, Any]:
    wanted = [symbol.strip().upper() for symbol in symbols if symbol.strip()][:MAX_SYMBOLS]
    chosen = [field for field in (fields or FIELDS) if field in FIELDS]
    path = panel_path(day, root)
    series: dict[str, dict[str, list[Any]]] = {symbol: {"minute": [], **{field: [] for field in chosen}} for symbol in wanted}
    if path.exists():
        import pyarrow.parquet as pq
        table = pq.read_table(path, columns=["minute", "symbol", *chosen], filters=[("symbol", "in", wanted)])
        for record in table.to_pylist():
            target = series.get(str(record["symbol"]))
            if target is not None:
                target["minute"].append(record["minute"].isoformat())
                for field in chosen:
                    target[field].append(record[field])
        source = "parquet"
    else:
        with database.transaction() as connection:
            documents = mcs.day_documents(connection, day)
        for observed_at, document in documents:
            for symbol, row in mcs.rows_for(document, wanted).items():
                series[symbol]["minute"].append(observed_at.isoformat())
                for field in chosen:
                    series[symbol][field].append(_value(row, FIELDS[field]))
        source = "documents"
    return {"trade_date": day.isoformat(), "source": source, "fields": chosen, "series": series,
            "symbols_capped_at": MAX_SYMBOLS, "research_only": True, "live_effect": "none"}


__all__ = ["FIELDS", "MAX_SYMBOLS", "export_day", "panel", "panel_path"]
