"""Pure aggregation and delivery policy for the opening-auction pulse.

The Longhu endpoints do not share one stable response schema.  This module
therefore keeps the source payload intact and derives only conservative,
source-labelled summaries.  A pulse is research evidence; it never writes a
strategy threshold or an order decision.
"""

from __future__ import annotations

from datetime import datetime, time
import hashlib
import json
import math
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")

PULSE_REQUESTS: tuple[dict[str, Any], ...] = (
    {"name": "auction", "target": "longhu_quote", "action": "MorningBiddingList", "controller": "HomeDingPan",
     "params": {"Order": 1, "a": "MorningBiddingList", "st": 300, "c": "HomeDingPan", "Index": 0, "PidType": 0, "apiv": "w41", "Type": 4}},
    {"name": "sector", "target": "longhu_quote", "action": "GetBKJJ_W36", "controller": "StockBidYiDong",
     "params": {"a": "GetBKJJ_W36", "c": "StockBidYiDong", "PhoneOSNew": 1, "VerSion": "5.20.0.8", "apiv": "w41"}},
    {"name": "plate", "target": "longhu_market_wide", "action": "GetPlateInfo_w38", "controller": "DailyLimitResumption",
     "params": {"a": "GetPlateInfo_w38", "st": 300, "c": "DailyLimitResumption", "Index": 0, "apiv": "w42"}},
    {"name": "breadth", "target": "longhu_market_wide", "action": "RiseFallAnalysis", "controller": "HomeDingPan",
     "params": {"a": "RiseFallAnalysis", "apiv": "w43", "c": "HomeDingPan", "PhoneOSNew": 1, "VerSion": "5.22.0.2"}},
    {"name": "mood", "target": "longhu_market_wide", "action": "MoodNumCount", "controller": "MarketMood",
     "params": {"a": "MoodNumCount", "apiv": "w43", "c": "MarketMood", "PhoneOSNew": 1, "VerSion": "5.22.0.2"}},
)

_UP = ("up", "rise", "上涨", "上涨家数", "up_count", "rise_count")
_DOWN = ("down", "fall", "下跌", "下跌家数", "down_count", "fall_count")
_LIMIT_UP = ("limit_up", "涨停", "zt", "涨停家数")
_LIMIT_DOWN = ("limit_down", "跌停", "dt", "跌停家数")
_NET = ("net_inflow", "main_net", "净流入", "净额", "主力净流入", "资金净流")
_CHANGE = ("change_pct", "涨跌幅", "涨幅", "涨跌")
_LABEL = ("name", "板块名称", "板块", "行业", "sector", "plate", "名称")


def auction_pulse_window(now: datetime) -> bool:
    local = now.astimezone(CN_TZ)
    return local.weekday() < 5 and time(9, 15) <= local.time() <= time(9, 30)


def _number(value: Any) -> float | None:
    if value in (None, "", "-", "--") or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _walk(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk(child)


def _field(row: Mapping[str, Any], aliases: tuple[str, ...]) -> float | None:
    lowered = {str(key).strip().lower(): value for key, value in row.items()}
    for alias in aliases:
        if alias.lower() in lowered:
            result = _number(lowered[alias.lower()])
            if result is not None:
                return result
    return None


def _text(row: Mapping[str, Any], aliases: tuple[str, ...]) -> str | None:
    lowered = {str(key).strip().lower(): value for key, value in row.items()}
    for alias in aliases:
        value = lowered.get(alias.lower())
        if value not in (None, ""):
            return str(value).strip()
    return None


def _rows(payload: Any) -> list[Any]:
    if not isinstance(payload, Mapping):
        return []
    pages = payload.get("pages")
    if isinstance(pages, list):
        rows: list[Any] = []
        for page in pages:
            if isinstance(page, Mapping) and "payload" in page:
                rows.extend(_rows(page.get("payload")))
            else:
                rows.extend(_rows(page))
        return rows
    for key in ("list", "data", "items", "rows", "bid", "trend"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return [payload]


def _summary_for_action(action: str, payload: Any) -> dict[str, Any]:
    mappings = [row for row in _walk(payload) if isinstance(row, Mapping)]
    up = down = limit_up = limit_down = net = None
    for row in mappings:
        up = up if up is not None else _field(row, _UP)
        down = down if down is not None else _field(row, _DOWN)
        limit_up = limit_up if limit_up is not None else _field(row, _LIMIT_UP)
        limit_down = limit_down if limit_down is not None else _field(row, _LIMIT_DOWN)
        net = net if net is not None else _field(row, _NET)
    result: dict[str, Any] = {"action": action, "rows": len(_rows(payload)), "raw_semantics": "provider_payload_preserved"}
    if up is not None:
        result["up"] = up
    if down is not None:
        result["down"] = down
    if limit_up is not None:
        result["limit_up"] = limit_up
    if limit_down is not None:
        result["limit_down"] = limit_down
    if net is not None:
        result["net_inflow"] = net
    if up is not None and down is not None and up + down > 0:
        result["breadth_ratio"] = round((up - down) / (up + down), 6)
    return result


def _sector_rows(payload: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in _rows(payload):
        if isinstance(item, Mapping):
            label = _text(item, _LABEL)
            change = _field(item, _CHANGE)
            net = _field(item, _NET)
            if label and (change is not None or net is not None):
                result.append({"label": label, "change_pct": change, "net_inflow": net})
        elif isinstance(item, (list, tuple)) and item:
            label = str(item[1] if len(item) > 1 else item[0]).strip()
            numbers = [_number(value) for value in item[2:]]
            numbers = [value for value in numbers if value is not None]
            if label and numbers:
                result.append({"label": label, "change_pct": numbers[0], "net_inflow": numbers[1] if len(numbers) > 1 else None})
    return result[:30]


def summarize_pulse(envelopes: Mapping[str, Any], observed_at: datetime) -> dict[str, Any]:
    """Create a conservative cross-source snapshot from gateway payloads."""
    summaries: dict[str, Any] = {}
    sectors: list[dict[str, Any]] = []
    for name, envelope in envelopes.items():
        payload = envelope.get("payload") if isinstance(envelope, Mapping) else envelope
        action = str(envelope.get("action") or name) if isinstance(envelope, Mapping) else name
        summaries[name] = _summary_for_action(action, payload)
        sectors.extend(_sector_rows(payload))
    sector_by_label: dict[str, dict[str, Any]] = {}
    for row in sectors:
        sector_by_label.setdefault(str(row["label"]), row)
    ordered = sorted(sector_by_label.values(), key=lambda row: abs(float(row.get("net_inflow") or row.get("change_pct") or 0)), reverse=True)
    breadth = next((item.get("breadth_ratio") for item in summaries.values() if item.get("breadth_ratio") is not None), None)
    net_values = [item.get("net_inflow") for item in summaries.values() if item.get("net_inflow") is not None]
    net_inflow = round(sum(float(value) for value in net_values), 6) if net_values else None
    return {
        "observed_at": observed_at.isoformat(),
        "exchange_window": "opening_call_auction",
        "sentiment": {"breadth_ratio": breadth, "status": "complete" if breadth is not None else "partial"},
        "money_flow": {"net_inflow": net_inflow, "status": "observed" if net_inflow is not None else "partial", "unit": "provider_native"},
        "sector_anomalies": ordered[:10],
        "sources": summaries,
        "research_only": True,
        "live_effect": "none",
    }


def _state_key(summary: Mapping[str, Any]) -> str:
    reduced = {
        "breadth": summary.get("sentiment", {}).get("breadth_ratio"),
        "net": summary.get("money_flow", {}).get("net_inflow"),
        "sectors": summary.get("sector_anomalies", [])[:3],
        "source_status": {
            str(name): str(value.get("status"))
            for name, value in (summary.get("source_status") or {}).items()
            if isinstance(value, Mapping)
        },
    }
    return hashlib.sha256(json.dumps(reduced, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]


def alert_decision(summary: Mapping[str, Any], previous: Mapping[str, Any] | None, *, now: datetime,
                   last_alert_at: datetime | None, cooldown_seconds: int = 30) -> dict[str, Any]:
    """Return an alert only for a changed state and outside the cooldown."""
    key = _state_key(summary)
    previous_key = _state_key(previous) if previous else None
    changed = previous_key != key
    cooled = last_alert_at is None or (now - last_alert_at).total_seconds() >= max(1, int(cooldown_seconds))
    should_send = changed and cooled
    return {"should_send": should_send, "state_key": key, "changed": changed, "cooled": cooled,
            "reason": "state_change" if should_send else "cooldown" if changed else "unchanged"}


def format_alert(summary: Mapping[str, Any]) -> str:
    sentiment = summary.get("sentiment") or {}
    flow = summary.get("money_flow") or {}
    sectors = summary.get("sector_anomalies") or []
    lines = ["【竞价脉冲｜研究观察】", "时间：" + str(summary.get("observed_at") or "unknown")]
    lines.append(f"情绪：breadth_ratio={sentiment.get('breadth_ratio', '缺失')}（{sentiment.get('status', 'partial')}）")
    lines.append(f"资金：net_inflow={flow.get('net_inflow', '缺失')}（provider_native，{flow.get('status', 'partial')}）")
    if sectors:
        labels = ", ".join(f"{item.get('label')}({item.get('change_pct', '—')}/{item.get('net_inflow', '—')})" for item in sectors[:5])
        lines.append("板块异动：" + labels)
    else:
        lines.append("板块异动：暂无可解释的板块数值，保留原始证据")
    lines.append("仅供研究与复盘，不构成交易指令")
    return "\n".join(lines)


__all__ = ["PULSE_REQUESTS", "alert_decision", "auction_pulse_window", "format_alert", "summarize_pulse"]
