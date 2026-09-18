#!/usr/bin/env python3
"""Refresh the sector map from Longhu, inside the quant-service container.

The strategy cannot name a candidate without knowing its sector: with
quant.sector_membership_history empty every pool member is rejected as
sector_core_unconfirmed, whatever else is working.

Run it from the peer:

    docker cp scripts/fill-longhu-sector-membership.py \
        trading-hareness-peer-quant-research-1:/tmp/fill.py
    docker exec trading-hareness-peer-quant-research-1 python /tmp/fill.py

Membership is read for the prior completed session, which is also the only
form the vendor serves - the live parameter shape is rejected outright
(errcode 1020). That costs nothing: a board's constituents do not change
during a session.

Point-in-time discipline applies to the result. Rows are stored with
known_at=now, and the strategy only admits membership known before 08:59:59 of
the session it is scanning, so a refresh run during the day takes effect on the
*next* session, not the current one. Run it after the close, or before 08:59.
"""

import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, "/app")

from app.longhu_sector_membership import (  # noqa: E402
    PROVIDER_KEY, TAXONOMY_KEY, fetch_catalog, gateway_rows, member_request,
)
from app.longhu_vendor_source import SharedLonghuReadSource, parse_industry_stock_row  # noqa: E402
from app.sector_membership_repository import persist_observed_snapshot  # noqa: E402
import app.main as service  # noqa: E402


def exchange_of(symbol: str) -> str:
    return "SH" if symbol.endswith(".SH") else "BJ" if symbol.endswith(".BJ") else "SZ"


def ensure_instrument(connection, symbol, row):
    connection.execute(
        "INSERT INTO quant.instruments(symbol,exchange,name,source) VALUES(%s,%s,%s,'longhuvip') "
        "ON CONFLICT(symbol) DO UPDATE SET name=coalesce(EXCLUDED.name,quant.instruments.name),updated_at=now()",
        (symbol, exchange_of(symbol), str(row.get("name") or "").strip() or None),
    )


def main() -> int:
    source = SharedLonghuReadSource()
    now = datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo("Asia/Shanghai")).date()

    with service.db.transaction() as connection:
        reference_date = connection.execute(
            "SELECT max(trading_date) AS d FROM quant.canonical_bars_daily WHERE trading_date < %s",
            (today,),
        ).fetchone()["d"]
    if reference_date is None:
        print("no completed session to read membership for")
        return 1
    print(f"membership as of {reference_date}")

    boards = fetch_catalog(source.raw_call)
    print(f"industry boards: {len(boards)}")
    if not boards:
        print("gateway returned no boards; nothing written")
        return 1

    # sector_membership_history carries a composite foreign key onto
    # quant.sectors, so the taxonomy and its boards must exist first.
    with service.db.transaction() as connection:
        connection.execute(
            """INSERT INTO quant.sector_taxonomies(taxonomy_key,label,provider_key,metadata)
               VALUES(%s,%s,%s,%s::jsonb)
               ON CONFLICT(taxonomy_key) DO UPDATE SET label=EXCLUDED.label,
                 provider_key=EXCLUDED.provider_key,metadata=EXCLUDED.metadata,updated_at=now()""",
            (TAXONOMY_KEY, "开盘啦行业板块", PROVIDER_KEY,
             '{"action": "ZhiShuStockList_W8", "semantic": "industry_membership"}'),
        )
        for board in boards:
            connection.execute(
                """INSERT INTO quant.sectors(taxonomy_key,sector_key,label,metadata)
                   VALUES(%s,%s,%s,%s::jsonb)
                   ON CONFLICT(taxonomy_key,sector_key) DO UPDATE
                     SET label=EXCLUDED.label,updated_at=now()""",
                (TAXONOMY_KEY, board["sector_key"], board["label"],
                 '{"source": "longhuvip:RealRankingInfo"}'),
            )

    stored_total, failures = 0, []
    for index, board in enumerate(boards, start=1):
        try:
            rows = gateway_rows(source.raw_call(member_request(board["sector_key"], reference_date)))
            members = [parsed for row in rows
                       if (parsed := parse_industry_stock_row(row, reference_date, board["sector_key"]))]
            if not members:
                failures.append((board["sector_key"], "no members"))
                continue
            with service.db.transaction() as connection:
                stored_total += persist_observed_snapshot(
                    connection, TAXONOMY_KEY, board["sector_key"], members, PROVIDER_KEY, now,
                    member_symbol=lambda row: row.get("symbol"), ensure_instrument=ensure_instrument,
                )
            if index % 20 == 0 or index == len(boards):
                print(f"  {index}/{len(boards)} boards, {stored_total} memberships")
        except Exception as error:  # noqa: BLE001 - one board must not end the refresh
            failures.append((board["sector_key"], f"{type(error).__name__}: {str(error)[:80]}"))

    with service.db.transaction() as connection:
        summary = connection.execute(
            "SELECT count(*) AS rows, count(DISTINCT sector_key) AS boards, "
            "count(DISTINCT symbol) AS symbols FROM quant.sector_membership_history "
            "WHERE taxonomy_key=%s", (TAXONOMY_KEY,),
        ).fetchone()
    print(f"stored {stored_total}; table now {dict(summary)}")
    if failures:
        print(f"failures ({len(failures)}): {failures[:5]}")
    return 0 if stored_total else 1


if __name__ == "__main__":
    raise SystemExit(main())
