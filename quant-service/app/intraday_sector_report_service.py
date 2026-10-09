"""Point-in-time sector report assembly from already fetched snapshots.

The external fetch orchestration remains in the router composition layer.  This
module owns only the synchronous database join and context projection, making
the expensive SQL contract independently testable and keeping provider calls
out of the database worker.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable

from .ths_concept_name_bridge import CATALOG_SQL, CATALOGS, FLOW_SOURCE, build_bridge

#: Daily board-flow taxonomies whose freshness the report carries, live first;
#: ``ths_industry``/``ths_concept_flow`` stopped refreshing on 2026-10-08.
BOARD_FLOW_CONTEXT_TAXONOMIES = ("longhu_ths_industry", "eastmoney_concept", "ths_industry", "ths_concept_flow")


def build_intraday_sector_report_from_membership(
    db: Any,
    kinds: tuple[str, ...],
    flow_parts: list[list[dict[str, Any]]],
    quotes: dict[str, dict[str, Any]],
    top_stocks: int,
    exchange_date: date,
    *,
    number: Callable[[Any], float | None],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[Any], list[Any], list[Any]]:
    """Join each live board's flow to its current members.

    The flows are 同花顺 boards (akshare reads data.10jqka.com.cn) keyed by
    name.  A board whose name matches the 同花顺 catalog of its kind takes
    that list's members (concept: Fuyao, then the frozen Tushare-era list;
    industry: Longhu, then Fuyao, then the frozen list); one that does not
    falls back to the Eastmoney board of the same label, as before.  Each item
    names the membership it used.
    """
    report: list[dict[str, Any]] = []
    coverage: dict[str, Any] = {}
    with db.transaction() as connection:
        for kind, flows in zip(kinds, flow_parts, strict=True):
            taxonomy_key = f"eastmoney_{kind}"
            bridge = build_bridge(kind, connection.execute(CATALOG_SQL, (list(CATALOGS[kind]),)).fetchall())
            labels = [str(flow.get("行业") or flow.get("板块名称") or "").strip() for flow in flows]
            matched, join_coverage = bridge.match(labels)
            codes = sorted(set(matched.values()))
            ths_members: dict[tuple[str, str], list[str]] = {}
            if codes:
                for row in connection.execute(
                    """SELECT taxonomy_key,sector_key,symbol FROM quant.sector_membership_history
                        WHERE taxonomy_key=ANY(%s) AND sector_key=ANY(%s) AND effective_to IS NULL""",
                    (list(CATALOGS[kind]), codes),
                ).fetchall():
                    ths_members.setdefault((str(row["taxonomy_key"]), str(row["sector_key"])), []).append(str(row["symbol"]))
            rows = connection.execute(
                """SELECT m.sector_key,m.symbol,s.label FROM quant.sector_membership_history m
                   JOIN quant.sectors s ON s.taxonomy_key=m.taxonomy_key AND s.sector_key=m.sector_key
                  WHERE m.taxonomy_key=%s AND m.effective_to IS NULL""",
                (taxonomy_key,),
            ).fetchall()
            by_sector: dict[str, list[str]] = {}
            key_by_label: dict[str, str] = {}
            for row in rows:
                by_sector.setdefault(str(row["sector_key"]), []).append(str(row["symbol"]))
                key_by_label[str(row["label"])] = str(row["sector_key"])
            covered = 0
            for flow, label in zip(flows, labels, strict=True):
                code = matched.get(label)
                membership_taxonomy = bridge.catalog_by_code.get(code) if code else None
                if code and membership_taxonomy and ths_members.get((membership_taxonomy, code)):
                    sector_key, members, join = code, ths_members[(membership_taxonomy, code)], "ths_board_name"
                else:
                    sector_key = str(flow.get("行业代码") or flow.get("板块代码") or key_by_label.get(label) or label).strip()
                    members, membership_taxonomy = by_sector.get(sector_key, []), taxonomy_key
                    join = "eastmoney_board_label" if members else "unmapped"
                stocks = [quotes[symbol] for symbol in members if symbol in quotes]
                stocks.sort(key=lambda item: (
                    item.get("main_net_inflow") is None,
                    -(item.get("main_net_inflow") or 0),
                    -(item.get("turnover") or 0),
                ))
                covered += int(bool(members))
                inflow, outflow = number(flow.get("流入资金")), number(flow.get("流出资金"))
                report.append({
                    "taxonomy_key": taxonomy_key, "sector_key": sector_key, "label": label,
                    "membership_taxonomy_key": membership_taxonomy, "membership_join": join,
                    "flow_source": FLOW_SOURCE,
                    "net_inflow": inflow - outflow if inflow is not None and outflow is not None else number(flow.get("净额")),
                    "change_pct": number(flow.get("行业-涨跌幅")),
                    "mapped_members": len(members), "quoted_members": len(stocks),
                    "top_stocks": stocks[:top_stocks], "member_quotes": stocks,
                })
            coverage[kind] = {"flow_boards": len(flows), "boards_with_members": covered,
                              "ths_name_join": {**join_coverage, "unmatched": join_coverage["unmatched"][:50]}}
        tushare_sector_context = connection.execute(
            """SELECT taxonomy_key,max(trading_date) AS latest_trade_date,count(*)::int AS rows
                 FROM quant.sector_market_observations
                WHERE taxonomy_key=ANY(%s)
                GROUP BY taxonomy_key ORDER BY taxonomy_key""",
            (list(BOARD_FLOW_CONTEXT_TAXONOMIES),),
        ).fetchall()
        tushare_stock_context = connection.execute(
            """SELECT api_name,max(NULLIF(row_data->>'trade_date','')) AS latest_trade_date,
                      count(DISTINCT row_data->>'ts_code')::int AS symbols,count(*)::int AS rows
                 FROM quant.tushare_raw_records
                WHERE api_name IN ('moneyflow','moneyflow_ths','moneyflow_dc')
                GROUP BY api_name ORDER BY api_name""",
        ).fetchall()
        tushare_realtime_context = connection.execute(
            """SELECT api_name,max(available_at) AS latest_available_at,count(*)::int AS rows
                 FROM quant.tushare_raw_records WHERE api_name IN ('rt_k','rt_min','rt_min_daily')
                GROUP BY api_name ORDER BY api_name""",
        ).fetchall()
    return report, coverage, tushare_sector_context, tushare_stock_context, tushare_realtime_context


__all__ = ["BOARD_FLOW_CONTEXT_TAXONOMIES", "build_intraday_sector_report_from_membership"]
