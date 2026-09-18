#!/usr/bin/env python3
"""Load THS concept/industry/region membership from Fuyao into the sector map.

Fuyao serves the complete THS index catalogue (2026-09-18: 390 concepts,
320 industries, 33 regions, 105 feature indices) and every index's full
constituent list in one call, so a whole taxonomy loads in minutes instead of
the hours the rate-limited ``ths_member`` backfill takes.

Taxonomies written (each its own key, never merged with another vendor's):
``fuyao_ths_concept``, ``fuyao_ths_industry``, ``fuyao_ths_region``.
They are research inputs; ``SECTOR_TAXONOMY_PREFERENCE`` (the strategy's
taxonomy choice) is deliberately left unchanged.

Point-in-time discipline is the repository's: rows get ``known_at=now``, so a
refresh during a session only counts from the next session.  Run it after the
close.  From the peer::

    docker cp scripts/fill-fuyao-ths-membership.py \\
        trading-hareness-peer-quant-research-scheduler-1:/tmp/fill.py
    docker exec trading-hareness-peer-quant-research-scheduler-1 python /tmp/fill.py --tags cn_concept

``--dry-run`` fetches and counts without writing.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app")

from app.datasources.sources.fuyao_evidence import index_catalog  # noqa: E402
from app.fuyao_provider import configured, fetch  # noqa: E402
from app.datasources.http import ashare_symbol  # noqa: E402
from app.sector_membership_repository import persist_observed_snapshot_batched  # noqa: E402

PROVIDER_KEY = "fuyao_ths"
TAXONOMIES = {
    "cn_concept": ("fuyao_ths_concept", "同花顺概念（Fuyao）"),
    "industry": ("fuyao_ths_industry", "同花顺行业（Fuyao）"),
    "region": ("fuyao_ths_region", "同花顺地域（Fuyao）"),
}


async def members_of(code: str) -> dict[str, dict]:
    data = await fetch("ths_index_constituents", {"thscode": code})
    rows = {}
    for item in data.get("item") or []:
        symbol = ashare_symbol(item.get("thscode"))
        if symbol:
            rows[symbol] = {"name": item.get("name"), "thscode": item.get("thscode"), "index_code": code}
    return rows


async def load(tag: str, dry_run: bool, delay: float) -> tuple[int, int, list]:
    import app.main as service  # noqa: PLC0415 - only needed when writing

    taxonomy_key, label = TAXONOMIES[tag]
    catalog = index_catalog(await fetch("ths_index_list", {"tag": tag}))
    print(f"{tag}: {len(catalog)} indices")
    now = datetime.now(timezone.utc)
    if not dry_run:
        with service.db.transaction() as connection:
            connection.execute(
                """INSERT INTO quant.sector_taxonomies(taxonomy_key,label,provider_key,metadata)
                   VALUES(%s,%s,%s,%s::jsonb)
                   ON CONFLICT(taxonomy_key) DO UPDATE SET label=EXCLUDED.label,
                     provider_key=EXCLUDED.provider_key,metadata=EXCLUDED.metadata,updated_at=now()""",
                (taxonomy_key, label, PROVIDER_KEY, f'{{"route": "ths_index_constituents", "tag": "{tag}"}}'),
            )
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO quant.sectors(taxonomy_key,sector_key,label,metadata)
                       VALUES(%s,%s,%s,'{"source": "fuyao:ths_index_list"}'::jsonb)
                       ON CONFLICT(taxonomy_key,sector_key) DO UPDATE SET label=EXCLUDED.label,updated_at=now()""",
                    [(taxonomy_key, code, name or code) for code, name in catalog.items()],
                )
    stored, empty, failures = 0, 0, []
    for index, code in enumerate(sorted(catalog), start=1):
        try:
            members = await members_of(code)
            if not members:
                empty += 1
            elif not dry_run:
                with service.db.transaction() as connection:
                    stored += persist_observed_snapshot_batched(
                        connection, taxonomy_key, code, members, PROVIDER_KEY, now, instrument_source=PROVIDER_KEY)
            else:
                stored += len(members)
        except Exception as error:  # noqa: BLE001 - one index must not end the refresh
            failures.append((code, f"{type(error).__name__}: {str(error)[:80]}"))
        if index % 50 == 0 or index == len(catalog):
            print(f"  {index}/{len(catalog)} indices, {stored} memberships")
        await asyncio.sleep(delay)
    return stored, empty, failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tags", default="cn_concept,industry", help="comma list of cn_concept,industry,region")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay-seconds", type=float, default=0.25)
    args = parser.parse_args()
    if not configured():
        print("Fuyao API key is not configured in this container")
        return 1
    total = 0
    for tag in [item.strip() for item in args.tags.split(",") if item.strip()]:
        if tag not in TAXONOMIES:
            parser.error(f"unknown tag {tag}")
        stored, empty, failures = asyncio.run(load(tag, args.dry_run, max(0.0, args.delay_seconds)))
        total += stored
        print(f"{tag}: stored {stored}, empty {empty}, failures {len(failures)} {failures[:3]}")
    return 0 if total else 1


if __name__ == "__main__":
    raise SystemExit(main())
