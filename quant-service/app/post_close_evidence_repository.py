"""Persisted post-close evidence reads for strategy and replay services.

This repository deliberately owns only the bounded database projections.  The
aggregation rules remain in :mod:`post_close_evidence`, keeping source
provenance and same-date joins explicit at the composition boundary.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .sector_membership_repository import point_in_time_membership_predicate
from .ths_concept_name_bridge import CATALOG_SQL, CATALOGS, FLOW_SOURCE, build_bridge


def _live_concept_rows(connection: Any, as_of_date: date) -> list[dict[str, Any]]:
    """The session's 同花顺 concept flow joined to point-in-time Fuyao membership by board name."""
    flows = [dict(row) for row in connection.execute(
        """SELECT sector_key,net_amount,change_pct,leading_label,provider_key,available_at
             FROM quant.sector_market_observations
            WHERE taxonomy_key='eastmoney_concept' AND trading_date=%s""",
        (as_of_date,),
    ).fetchall()]
    if not flows:
        return []
    bridge = build_bridge("concept", connection.execute(CATALOG_SQL, (list(CATALOGS["concept"]),)).fetchall())
    matched, _coverage = bridge.match(row["sector_key"] for row in flows)
    flow_by_code: dict[str, dict[str, Any]] = {}
    for row in flows:
        code = matched.get(str(row["sector_key"]).strip())
        if code is not None:
            flow_by_code.setdefault(code, row)
    if not flow_by_code:
        return []
    members = connection.execute(
        f"""SELECT member.symbol,member.sector_key FROM quant.sector_membership_history member
             WHERE member.taxonomy_key='fuyao_ths_concept' AND {point_in_time_membership_predicate("member")}
               AND member.sector_key=ANY(%s)""",
        (as_of_date, as_of_date, as_of_date, sorted(flow_by_code)),
    ).fetchall()
    rows = []
    for member in members:
        code = str(member["sector_key"])
        flow = flow_by_code[code]
        rows.append({
            "symbol": member["symbol"], "sector_key": code, "label": bridge.name_for(code) or flow["sector_key"],
            "net_amount": flow["net_amount"], "change_pct": flow["change_pct"], "leading_label": flow["leading_label"],
            "provider_key": flow["provider_key"], "available_at": flow["available_at"],
            "taxonomy_key": "fuyao_ths_concept", "flow_taxonomy_key": "eastmoney_concept",
            "flow_board_name": flow["sector_key"], "flow_source": FLOW_SOURCE, "net_amount_unit": "100m_cny",
        })
    return rows


def load_exact_board_context_rows(database: Any, as_of_date: date) -> list[dict[str, Any]]:
    """Return same-date exact board membership joined to saved board flow.

    Concept flow first: the session's 同花顺 concept flow (stored as
    ``eastmoney_concept``, keyed by board name) over point-in-time
    ``fuyao_ths_concept`` membership, joined by name; the Tushare-era
    ``ths_concept_flow`` rows still answer the sessions they cover.  The
    Longhu full-market close source supplies an exact THS industry
    ``plate_id`` on every saved stock-flow row, so it is a valid same-date
    fallback for a symbol no concept covers.  Concept net amounts are in 亿元
    and Longhu's in yuan; each row names its unit.  All branches are persisted
    evidence reads; this function never calls a provider.
    """
    membership_predicate = point_in_time_membership_predicate("member")
    with database.transaction() as connection:
        rows = connection.execute(
            f"""WITH exact_concepts AS (
                   SELECT member.symbol,flow.sector_key,sector.label,flow.net_amount,flow.change_pct,
                          flow.leading_label,flow.provider_key,flow.available_at,'ths_concept_flow'::text AS taxonomy_key
                     FROM quant.sector_membership_history member
                     JOIN quant.sector_market_observations flow
                       ON flow.taxonomy_key=member.taxonomy_key AND flow.sector_key=member.sector_key
                     JOIN quant.sectors sector
                       ON sector.taxonomy_key=flow.taxonomy_key AND sector.sector_key=flow.sector_key
                    WHERE member.taxonomy_key='ths_concept_flow' AND {membership_predicate}
                      AND flow.taxonomy_key='ths_concept_flow' AND flow.trading_date=%s
               ), latest_longhu_report AS (
                   SELECT observed_at,payload
                     FROM quant.intraday_board_reports
                    WHERE status='completed'
                      AND (observed_at AT TIME ZONE 'Asia/Shanghai')::date=%s
                      AND source_status->>'provider'='longhuvip_composite'
                    ORDER BY observed_at DESC LIMIT 1
               ), longhu_boards AS (
                   SELECT item->>'sector_key' AS sector_key,item->>'label' AS label,
                          nullif(item->>'net_inflow','')::numeric AS net_amount,
                          nullif(item->>'change_pct','')::numeric AS change_pct,
                          item#>>'{{top_stocks,0,name}}' AS leading_label,
                          'longhuvip_composite'::text AS provider_key,report.observed_at AS available_at,
                          'longhu_ths_industry'::text AS taxonomy_key
                     FROM latest_longhu_report report
                     CROSS JOIN LATERAL jsonb_array_elements(coalesce(report.payload->'items','[]'::jsonb)) item
               ), exact_longhu_industry AS (
                   SELECT stock.symbol,board.sector_key,board.label,board.net_amount,board.change_pct,
                          board.leading_label,board.provider_key,board.available_at,board.taxonomy_key
                     FROM quant.stock_money_flow_daily stock
                     JOIN longhu_boards board ON board.sector_key=stock.raw->>'plate_id'
                    WHERE stock.trading_date=%s AND stock.provider='longhuvip_composite'
                      AND stock.source='longhuvip_main_net'
               )
               SELECT * FROM exact_concepts
               UNION ALL
               SELECT longhu.* FROM exact_longhu_industry longhu
                WHERE NOT EXISTS (
                    SELECT 1 FROM exact_concepts concept WHERE concept.symbol=longhu.symbol
                )""",
            (as_of_date, as_of_date, as_of_date, as_of_date, as_of_date, as_of_date),
        ).fetchall()
        live = _live_concept_rows(connection, as_of_date)
    covered = {row["symbol"] for row in live}
    stored = [{**dict(row), "net_amount_unit": "yuan" if dict(row).get("taxonomy_key") == "longhu_ths_industry" else "100m_cny"}
              for row in rows]
    return live + [row for row in stored if not (row.get("taxonomy_key") == "longhu_ths_industry" and row["symbol"] in covered)]


def lhb_event_rows(connection: Any, trade_date: date, *, available_by: datetime | None = None) -> list[dict[str, Any]]:
    """Return the stored Fuyao dragon-tiger rows of ``trade_date``, newest capture first.

    The session is the list's own ``trade_date``, not the capture clock: a
    list that publishes late is first captured on a later evening and still
    belongs to its own session.  With ``available_by`` only rows captured by
    then are visible.  A body that is not valid JSON is skipped instead of
    failing the read.
    """
    rows = connection.execute(
        """SELECT symbol,source,available_at,payload FROM (
               SELECT symbol,source,available_at,CASE WHEN body IS JSON THEN body::jsonb END AS payload
                 FROM quant.market_events
                WHERE event_type='lhb_ths' AND source='fuyao_ths'
                  AND (%s::timestamptz IS NULL OR available_at<=%s)
           ) event
          WHERE payload->>'trade_date'=%s
          ORDER BY available_at DESC,symbol""",
        (available_by, available_by, trade_date.isoformat()),
    ).fetchall()
    return [dict(row) for row in rows]


def load_lhb_context_rows(database: Any, as_of_date: date) -> list[dict[str, Any]]:
    """Return every stored dragon-tiger row of the session (see :func:`lhb_event_rows`)."""
    with database.transaction() as connection:
        return lhb_event_rows(connection, as_of_date)


__all__ = ["lhb_event_rows", "load_exact_board_context_rows", "load_lhb_context_rows"]
