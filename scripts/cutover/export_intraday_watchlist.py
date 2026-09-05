"""Export only explicit intraday watchlist control rows as JSON.

This is a cutover utility.  It intentionally exports no bars, observations,
signals, credentials, or analyst evidence.
"""

from __future__ import annotations

import json
import os

import psycopg


def main() -> None:
    with psycopg.connect(
        host=os.getenv("PGHOST", "127.0.0.1"),
        port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "quant_intraday_edge"),
        user=os.getenv("PGUSER", "quant_edge"),
        password=os.getenv("PGPASSWORD", ""),
    ) as connection:
        rows = connection.execute(
            """SELECT symbol,label,enabled,alert_on_entry,alert_on_exit,
                      entry_price,available_quantity,hard_stop,take_profit,metadata
                 FROM quant.intraday_watchlists ORDER BY symbol"""
        ).fetchall()
        columns = (
            "symbol", "label", "enabled", "alert_on_entry", "alert_on_exit",
            "entry_price", "available_quantity", "hard_stop", "take_profit", "metadata",
        )
    json.dump([dict(zip(columns, row)) for row in rows], fp=os.sys.stdout, default=str, ensure_ascii=False)


if __name__ == "__main__":
    main()
