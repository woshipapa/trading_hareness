"""Deterministic, source-preserving merge for persisted limit-up pools."""

from __future__ import annotations

import json
import re
from typing import Any, Callable


_SYMBOL = re.compile(r"\d{6}\.(SH|SZ|BJ)")


def _field(raw: dict[str, Any], *keys: str) -> Any:
    """First present value: other sources name the same fact in Chinese or English."""
    for key in keys:
        if raw.get(key) not in (None, ""):
            return raw[key]
    return None


def merge_limit_pool_sources(
    ths_rows: list[dict[str, Any]],
    eastmoney_rows: list[dict[str, Any]],
    *,
    json_safe: Callable[[Any], Any],
    number: Callable[[Any], float | None],
) -> dict[str, Any]:
    """Build a source-labelled union without claiming exchange completeness.

    ``ths_rows`` is the THS pool, already in limit-pool row shape; since
    ``limit_list_ths`` stopped (decision 0005) it is the Fuyao close snapshot,
    labelled by each row's own ``provider_key``.  ``eastmoney_rows``
    are raw ``limit_up_pool`` events of the other sources.  A source can
    enrich a matching security only when the primary source did not provide
    that field.  Chinese labels are never used for joins: the canonical
    six-digit exchange code is the sole identity.  The coverage keys keep
    their historical ``tushare_*``/``eastmoney_*`` names because the console
    reads them; ``primary_sources``/``secondary_sources`` say what they count.
    """
    merged: dict[str, dict[str, Any]] = {}
    ths_symbols: set[str] = set()
    eastmoney_symbols: set[str] = set()
    market_event_symbols: set[str] = set()
    primary_sources: set[str] = set()
    secondary_sources: set[str] = set()
    for stored in ths_rows:
        raw = dict(stored.get("row_data") or stored)
        symbol = str(raw.get("ts_code") or "").upper()
        if not _SYMBOL.fullmatch(symbol):
            continue
        ths_symbols.add(symbol)
        provider_key = str(stored.get("provider_key") or "unknown")
        primary_sources.add(provider_key)
        merged[symbol] = {
            **json_safe(raw), "ts_code": symbol, "provider_key": provider_key,
            "available_at": stored.get("available_at"), "sources": [provider_key],
        }
    for stored in eastmoney_rows:
        symbol = str(stored.get("symbol") or "").upper()
        if not _SYMBOL.fullmatch(symbol):
            continue
        eastmoney_symbols.add(symbol)
        event_type = str(stored.get("event_type") or "")
        source_name = str(stored.get("source") or "")
        is_market_event = event_type == "limit_up_pool" or source_name.startswith("fuyao")
        if is_market_event:
            market_event_symbols.add(symbol)
        body = stored.get("body") or {}
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                body = {}
        raw = dict(body) if isinstance(body, dict) else {}
        board_count = int(number(raw.get("连板数")) or 1)
        source_tag = f"market_events:{source_name or 'unknown'}" if is_market_event else "eastmoney_stock_zt_pool_em"
        secondary_sources.add(source_tag)
        eastmoney = {
            "ts_code": symbol, "name": _field(raw, "名称", "name"), "limit_type": "涨停池",
            "pct_chg": number(_field(raw, "涨跌幅", "pct_change")), "price": number(_field(raw, "最新价", "price")),
            "amount": number(_field(raw, "成交额", "amount")), "turnover_rate": number(_field(raw, "换手率", "turnover_rate")),
            "limit_amount": number(raw.get("封板资金")), "first_time": raw.get("首次封板时间"),
            "last_time": raw.get("最后封板时间"), "open_num": number(raw.get("炸板次数")),
            "tag": "首板" if board_count <= 1 else f"{board_count}连板",
            "lu_desc": raw.get("所属行业"), "provider_key": stored.get("source") or "akshare",
            "available_at": stored.get("available_at"),
            "sources": [source_tag],
            "source_fallback": is_market_event,
        }
        if symbol in merged:
            for key, value in eastmoney.items():
                if key not in {"provider_key", "available_at", "sources"} and merged[symbol].get(key) in {None, ""} and value not in {None, ""}:
                    merged[symbol][key] = value
            merged[symbol]["sources"] = [*merged[symbol].get("sources", []), source_tag]
            merged[symbol]["eastmoney_evidence"] = json_safe(eastmoney)
        else:
            merged[symbol] = eastmoney
    union_symbols = ths_symbols | eastmoney_symbols
    return {
        "items": list(merged.values()),
        "coverage": {
            "status": (
                "two_source_union" if ths_symbols and eastmoney_symbols
                else "market_event_fallback" if market_event_symbols and not ths_symbols
                else "single_source_only" if union_symbols
                else "unavailable"
            ),
            "union_count": len(union_symbols), "intersection_count": len(ths_symbols & eastmoney_symbols),
            "tushare_count": len(ths_symbols), "eastmoney_count": len(eastmoney_symbols),
            "tushare_only": sorted(ths_symbols - eastmoney_symbols),
            "eastmoney_only": sorted(eastmoney_symbols - ths_symbols), "local_truncation": False,
            "primary_sources": sorted(primary_sources), "secondary_sources": sorted(secondary_sources),
            "notice": "同花顺侧为 Fuyao 收盘集合竞价阶段最后一次涨停池快照（原 limit_list_ths 已停用），"
                      "另一侧为其它来源的同日涨停事件；并集只表示本地已采集证据，不代表交易所官方全量保证。",
        },
    }


__all__ = ["merge_limit_pool_sources"]
