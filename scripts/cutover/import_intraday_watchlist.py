"""Import explicit intraday watchlist control rows into the owner database."""

from __future__ import annotations

import json
import os
import sys

import psycopg
from psycopg.types.json import Json


def main() -> None:
    rows = json.load(sys.stdin)
    with psycopg.connect(
        host=os.getenv("PGHOST", "db-tunnel"),
        port=os.getenv("PGPORT", "5432"),
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
    ) as connection:
        with connection.cursor() as cursor:
            for row in rows:
                symbol = str(row["symbol"]).upper()
                cursor.execute(
                    """INSERT INTO quant.instruments(symbol,exchange,name,source)
                       VALUES(%s,%s,%s,'edge_watchlist_cutover')
                       ON CONFLICT(symbol) DO UPDATE
                       SET name=COALESCE(EXCLUDED.name,quant.instruments.name)""",
                    (symbol, symbol[-2:], row.get("label")),
                )
                cursor.execute(
                    """INSERT INTO quant.intraday_watchlists(
                         symbol,label,enabled,alert_on_entry,alert_on_exit,
                         entry_price,available_quantity,hard_stop,take_profit,metadata)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(symbol) DO UPDATE SET
                         label=EXCLUDED.label, enabled=EXCLUDED.enabled,
                         alert_on_entry=EXCLUDED.alert_on_entry,
                         alert_on_exit=EXCLUDED.alert_on_exit,
                         entry_price=EXCLUDED.entry_price,
                         available_quantity=EXCLUDED.available_quantity,
                         hard_stop=EXCLUDED.hard_stop,
                         take_profit=EXCLUDED.take_profit,
                         metadata=EXCLUDED.metadata, updated_at=now()""",
                    (
                        symbol, row.get("label"), row.get("enabled", True),
                        row.get("alert_on_entry", True), row.get("alert_on_exit", True),
                        row.get("entry_price"), row.get("available_quantity", 0),
                        row.get("hard_stop"), row.get("take_profit"),
                        Json(row.get("metadata") or {}),
                    ),
                )
        connection.commit()
        counts = connection.execute(
            "SELECT count(*)::int, count(*) FILTER (WHERE enabled)::int "
            "FROM quant.intraday_watchlists"
        ).fetchone()
    print(json.dumps({"imported": len(rows), "total": counts[0], "enabled": counts[1]}))


if __name__ == "__main__":
    main()
