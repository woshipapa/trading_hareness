"""Daily board observations materialized from evidence the service already holds.

Reads the Longhu full-market close's board report (``intraday_board_reports``)
and the one-minute board-flow capture (``intraday_board_flow_snapshots``) and
writes ``quant.sector_market_observations`` rows, each under the taxonomy of
the vendor that produced it.  No provider is called.

The taxonomy and board rows of ``longhu_ths_industry`` and
``eastmoney_concept`` belong to their membership writers, so they are only
inserted when missing and never relabelled from here.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from psycopg.types.json import Json

from .sector_catalog_repository import upsert_sectors


LONGHU_PROVIDER_KEY = "longhuvip_composite"
_CN_TZ = ZoneInfo("Asia/Shanghai")
#: The capture runs to 15:00; the close is the last snapshot from 14:55 on.
CLOSING_WINDOW = (time(14, 55), time(15, 1))


def decimal_value(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def longhu_close_boards(database: Any, trade_date: date) -> tuple[datetime | None, list[dict[str, Any]]]:
    """The session's completed Longhu close board report, newest first."""
    with database.transaction() as connection:
        row = connection.execute(
            """SELECT observed_at,payload->'items' AS items FROM quant.intraday_board_reports
                WHERE status='completed' AND (observed_at AT TIME ZONE 'Asia/Shanghai')::date=%s
                  AND source_status->>'provider'=%s
                ORDER BY observed_at DESC LIMIT 1""",
            (trade_date, LONGHU_PROVIDER_KEY),
        ).fetchone()
    if not row:
        return None, []
    return row["observed_at"], [dict(item) for item in row["items"] or [] if isinstance(item, Mapping)]


def concept_close_snapshot(
    database: Any, trade_date: date,
) -> tuple[datetime | None, list[dict[str, Any]], dict[str, Any]]:
    """The last closing-window snapshot holding concept boards, and its context.

    A snapshot taken before 14:55 is not the session's flow: if the capture
    stopped early the day has no closing value and the context names the
    latest concept snapshot there was instead.
    """
    start, end = (datetime.combine(trade_date, moment, tzinfo=_CN_TZ) for moment in CLOSING_WINDOW)
    with database.transaction() as connection:
        row = connection.execute(
            """SELECT observed_at,payload FROM quant.intraday_board_flow_snapshots
                WHERE observed_at>=%s AND observed_at<%s AND status IN ('completed','partial')
                  AND coalesce((coverage->'concept'->>'flow_boards')::int,0)>0
                ORDER BY observed_at DESC LIMIT 1""",
            (start, end),
        ).fetchone()
        if not row:
            latest = connection.execute(
                """SELECT max(observed_at) at FROM quant.intraday_board_flow_snapshots
                    WHERE observed_at>=%s AND observed_at<%s
                      AND coalesce((coverage->'concept'->>'flow_boards')::int,0)>0""",
                (datetime.combine(trade_date, time(0, 0), tzinfo=_CN_TZ),
                 datetime.combine(trade_date, time(0, 0), tzinfo=_CN_TZ) + timedelta(days=1)),
            ).fetchone()
            return None, [], {"latest_concept_snapshot_at": latest["at"].isoformat() if latest and latest["at"] else None}
    payload = dict(row["payload"] or {})
    items = [dict(item) for item in payload.get("items") or []
             if isinstance(item, Mapping) and item.get("taxonomy_key") == "eastmoney_concept"
             and item.get("sector_key") and item.get("net_inflow") is not None]
    return row["observed_at"], items, {
        "unit": payload.get("unit"), "provider": (payload.get("providers") or {}).get("concept") or "eastmoney_free",
    }


_INSERT_MISSING_SECTORS = """
    INSERT INTO quant.sectors(taxonomy_key,sector_key,label,metadata)
    SELECT %s,s.sector_key,s.label,%s::jsonb FROM unnest(%s::text[],%s::text[]) AS s(sector_key,label)
     ORDER BY s.sector_key
    ON CONFLICT(taxonomy_key,sector_key) DO NOTHING
"""


def persist_board_observations(
    database: Any,
    *,
    taxonomy_key: str,
    taxonomy_label: str,
    provider_key: str,
    trade_date: date,
    available_at: datetime,
    rows: Sequence[Mapping[str, Any]],
    owns_taxonomy: bool,
    taxonomy_metadata: Mapping[str, Any],
) -> int:
    """Upsert one session's board rows.

    ``owns_taxonomy`` marks a derived taxonomy this writer alone fills: its
    board labels follow the source and the session is replaced as a whole,
    so a board that fell out does not keep a stale row.
    """
    by_key = {str(row["sector_key"]): row for row in rows}
    keys = sorted(by_key)
    with database.transaction() as connection:
        connection.execute(
            f"""INSERT INTO quant.sector_taxonomies(taxonomy_key,label,provider_key,metadata) VALUES(%s,%s,%s,%s)
                ON CONFLICT(taxonomy_key) DO {'UPDATE SET label=EXCLUDED.label,provider_key=EXCLUDED.provider_key,metadata=EXCLUDED.metadata,updated_at=now()' if owns_taxonomy else 'NOTHING'}""",
            (taxonomy_key, taxonomy_label, provider_key, Json(dict(taxonomy_metadata))),
        )
        if owns_taxonomy:
            upsert_sectors(connection, taxonomy_key, ((key, str(by_key[key].get("label") or key), {"source": taxonomy_metadata.get("derivation")}) for key in keys))
            connection.execute(
                "DELETE FROM quant.sector_market_observations WHERE taxonomy_key=%s AND trading_date=%s AND provider_key=%s",
                (taxonomy_key, trade_date, provider_key),
            )
        elif keys:
            connection.execute(_INSERT_MISSING_SECTORS, (
                taxonomy_key, Json({"source": f"{provider_key}:board_flow"}), keys,
                [str(by_key[key].get("label") or key) for key in keys],
            ))
        if keys:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO quant.sector_market_observations(taxonomy_key,sector_key,trading_date,provider_key,available_at,
                             close,change_pct,net_amount,net_buy_amount,net_sell_amount,constituent_count,leading_symbol,leading_label,raw)
                       VALUES(%s,%s,%s,%s,%s,NULL,%s,%s,NULL,NULL,%s,%s,%s,%s)
                       ON CONFLICT(taxonomy_key,sector_key,trading_date,provider_key) DO UPDATE SET
                         available_at=EXCLUDED.available_at,change_pct=EXCLUDED.change_pct,net_amount=EXCLUDED.net_amount,
                         constituent_count=EXCLUDED.constituent_count,leading_symbol=EXCLUDED.leading_symbol,
                         leading_label=EXCLUDED.leading_label,raw=EXCLUDED.raw""",
                    [(taxonomy_key, key, trade_date, provider_key, available_at,
                      decimal_value(by_key[key].get("change_pct")), decimal_value(by_key[key].get("net_amount")),
                      by_key[key].get("constituent_count"), by_key[key].get("leading_symbol"),
                      by_key[key].get("leading_label"), Json(dict(by_key[key].get("raw") or {})))
                     for key in keys],
                )
    return len(keys)


__all__ = [
    "CLOSING_WINDOW", "LONGHU_PROVIDER_KEY", "concept_close_snapshot", "decimal_value", "longhu_close_boards",
    "persist_board_observations",
]
