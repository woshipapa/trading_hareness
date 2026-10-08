"""Write a whole board directory in one statement.

The catalog syncs used to upsert one sector per round trip inside a single
transaction. Over the owner tunnel that held the taxonomy's row locks for
minutes (a 428-second transaction on 2026-10-08) and blocked every other
writer of the same taxonomy, including a restarting service. One statement,
in sector_key order, keeps the transaction short and the lock order the same
for every writer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
from typing import Any

_UPSERT_SECTORS = """
    INSERT INTO quant.sectors(taxonomy_key,sector_key,label,metadata)
    SELECT %s,s.sector_key,s.label,s.metadata::jsonb
      FROM unnest(%s::text[],%s::text[],%s::text[]) AS s(sector_key,label,metadata)
     ORDER BY s.sector_key
    ON CONFLICT(taxonomy_key,sector_key) DO UPDATE
      SET label=EXCLUDED.label,metadata=EXCLUDED.metadata,updated_at=now()
"""


def upsert_sectors(connection: Any, taxonomy_key: str,
                   sectors: Iterable[tuple[str, str, Mapping[str, Any]]]) -> int:
    """Upsert ``(sector_key, label, metadata)`` rows; the last row for a key wins."""
    latest: dict[str, tuple[str, str]] = {}
    for sector_key, label, metadata in sectors:
        latest[str(sector_key)] = (
            str(label), json.dumps(dict(metadata), ensure_ascii=False, sort_keys=True, default=str))
    if not latest:
        return 0
    keys = sorted(latest)
    connection.execute(_UPSERT_SECTORS, (
        taxonomy_key, keys, [latest[key][0] for key in keys], [latest[key][1] for key in keys],
    ))
    return len(keys)


__all__ = ["upsert_sectors"]
