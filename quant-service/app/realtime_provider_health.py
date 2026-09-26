"""Pure, source-specific health projection; no provider I/O or trading effects."""

from datetime import datetime, time, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

CN = ZoneInfo("Asia/Shanghai")
STATES = ("healthy", "partial", "degraded", "unavailable", "stale", "standby",
          "not_tested", "unconfigured", "disabled", "circuit_open", "unknown")
# key, physical provider, capability, label, scope, observation freshness budget
SOURCES = (
    ("longhuvip", "longhuvip", "stock_quote", "Longhu 报价", "watchlist", 90),
    ("tencent_free", "tencent_free", "realtime_quote", "腾讯报价", "watchlist", 90),
    ("sina_free", "sina_free", "realtime_quote", "新浪补缺报价", "fallback", 90),
    ("fuyao_ths", "fuyao_ths", "realtime_quote", "Fuyao 全 A 快照", "all_a", 120),
    ("longhuvip_minute", "longhuvip", "intraday_minute", "Longhu 分钟", "priority_subset", 90),
    ("tencent_minute", "tencent_free", "intraday_minute", "腾讯分钟", "priority_subset", 90),
    ("tushare_super_get", "tushare_super_get", "rt_k", "Super GET 报价交叉确认", "rotation", 90),
    ("tushare_super_get_rt_min", "tushare_super_get", "rt_min", "Super GET 分钟", "rotation", 90),
    ("tushare_super_sdk_rt_min", "tushare_super_sdk", "rt_min", "Super SDK 分钟", "rotation", 90),
    ("eastmoney_free", "eastmoney_free", "watchlist_flow_quote", "东财个股资金", "watchlist", 90),
    ("eastmoney_board_flow", "eastmoney_free", "board_flow", "东财板块资金", "boards", 360),
)


def mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def timestamp(value: Any) -> datetime | None:
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # A timezone-free persisted event is not trustworthy freshness evidence.
        return result.astimezone(timezone.utc) if result.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def count(value: Any) -> int | None:
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    try:
        return max(0, int(value)) if value is not None and not isinstance(value, bool) else None
    except (ValueError, TypeError, OverflowError):
        return None


def first_count(data: Mapping[str, Any], *keys: str) -> int | None:
    return next((n for k in keys if (n := count(data.get(k))) is not None), None)


def source_observation(key: str, sources: dict[str, Any], watches: int | None) -> dict[str, Any]:
    """Normalize counts in their own scopes, never borrowing another source."""
    paths = {"longhuvip": "longhuvip_watch", "tencent_free": "tencent_watch",
             "sina_free": "tencent_watch", "fuyao_ths": "fuyao",
             "tushare_super_get": "tushare_rt_k_fast", "eastmoney_free": "eastmoney_watch_flow",
             "eastmoney_board_flow": "eastmoney_board_flow"}
    data = mapping(sources.get(paths.get(key, key)))
    requested = first_count(data, "requested")
    received = first_count(data, "received", "matched_symbols", "rows")
    valid = first_count(data, "eligible_symbols", "decision_eligible_symbols")
    status = str(data.get("status") or data.get("provider_status") or "unknown")
    if key in {"longhuvip", "eastmoney_free"}:
        requested = requested if requested is not None else watches
    elif key == "tencent_free":
        requested = watches
        received = first_count(data, "fresh_watch_quote_symbols", "fresh_watch_quote_rows")
        valid = first_count(data, "decision_eligible_watch_quote_symbols")
        if received is None:
            received = valid
    elif key == "sina_free":
        received = first_count(data, "sina_watch_quote_rows")
        requested = first_count(data, "missing_direct_watch_quote_symbols")
        status = "completed" if received else "not_tested"
    elif key == "fuyao_ths":
        snapshot = mapping(data.get("all_a_snapshot"))
        requested = first_count(snapshot, "total")
        received = first_count(snapshot, "matched_rows") if snapshot else received
        data = {**data, **snapshot}
        status = str(snapshot.get("status") or status)
    elif key in {"longhuvip_minute", "tencent_minute"}:
        context = mapping(sources.get("tencent_minute_context"))
        branch = "primary" if key == "longhuvip_minute" else "fallback"
        data = mapping(context.get(branch))
        # Older Tencent-only evidence predates the primary/fallback envelope.
        if not data and key == "tencent_minute" and context.get("provider") == "tencent_free":
            data = context
        requested, received = count(data.get("requested")), count(data.get("completed"))
        status = str(data.get("provider_status") or "unknown")
    elif key == "tushare_super_get":
        counts = mapping(data.get("status_counts"))
        requested = sum(count(v) or 0 for v in counts.values())
        received = count(counts.get("confirmed")) or 0
        valid = received
        status = "completed" if requested else "not_tested"
    elif key.endswith("_rt_min"):
        provider = key.removesuffix("_rt_min")
        context = mapping(sources.get("tushare_rt_min"))
        rows = [mapping(row) for row in mapping(context.get("items")).values()
                if mapping(row).get("provider") in {provider, provider.removeprefix("tushare_")}]
        requested = len(rows)
        received = sum((first_count(row, "fresh_rows", "received", "received_rows") or 0) > 0
                       and row.get("status") in {"completed", "fresh", "cached"} for row in rows)
        status = "completed" if requested else "not_tested"
        data = {}
    elif key == "eastmoney_board_flow":
        # A cache timestamp without row/coverage evidence does not prove a feed.
        requested = first_count(data, "requested")
        received = first_count(data, "rows", "received")
    return {"status": status, "requested": requested, "received": received, "valid": valid,
            "evidence_limit": count(data.get("max_symbols")), "truncated": bool(data.get("truncated")),
            "observed_at": data.get("observed_at"), "age_at_capture": data.get("age_seconds"),
            "latency_ms": data.get("latency_ms"), "error_present": bool(data.get("error") or data.get("errors"))}


def observation_state(obs: dict[str, Any]) -> str:
    status, received, requested = obs["status"], obs["received"], obs["requested"]
    if status in {"disabled", "unconfigured", "not_tested", "stale"}:
        return status
    if status in {"failed", "unavailable", "error"}:
        return "degraded" if received else "unavailable"
    if received == 0:
        return "unavailable" if requested else "not_tested"
    if status not in {"completed", "fresh", "cached", "partial", "degraded"} or received is None:
        return "unknown"
    if obs["error_present"] or status in {"partial", "degraded"} or obs["truncated"]:
        return "partial"
    if requested and received < requested or obs["valid"] is not None and obs["valid"] < received:
        return "partial"
    return "healthy"


def project_realtime_source_health(
    provider_health: Mapping[str, Any], latest_scan: Mapping[str, Any] | None, *,
    provider_configs: list[dict[str, Any]] | None = None, session_active: bool | None = None,
    runtime_limits: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    now = timestamp(provider_health.get("observed_at")) or datetime.now(timezone.utc)
    scan = mapping(latest_scan)
    sources = mapping(scan.get("source_status"))
    watches = first_count(mapping(scan.get("summary")), "watched")
    if watches is None:
        watches = count(scan.get("requested_symbols"))
    configs = {row["provider_key"]: row.get("configured") for row in provider_configs or []}
    limits = dict(runtime_limits or {})
    items = []
    for key, provider, capability, label, scope, budget in SOURCES:
        obs = source_observation(key, sources, watches)
        historical_state = observation_state(obs)
        stamp = timestamp(obs["observed_at"] or scan.get("observed_at"))
        age = (now-stamp).total_seconds() if stamp else None
        try:
            age = age + max(0, float(obs["age_at_capture"] or 0)) if age is not None else None
        except (TypeError, ValueError):
            age = None
        state = historical_state
        reason = f"latest observation: {historical_state}"
        if state not in {"unknown", "not_tested", "unconfigured", "disabled"}:
            if age is None or age < -30:
                state, reason = "unknown", "missing or future evidence timestamp"
            elif age > (budget if session_active else 86400):
                state, reason = "stale", "recorded evidence exceeds freshness budget"
            elif state == "healthy" and session_active is not True:
                state, reason = ("standby", "outside market session; last observation succeeded") if session_active is False else ("unknown", "exchange session has not been verified")
        rows = [r for r in provider_health.get("items", []) if r.get("provider_key") == provider
                and r.get("capability") in {capability, "realtime_quote" if capability == "stock_quote" else capability}]
        row = max(rows, key=lambda r: max(timestamp(r.get("last_success_at")) or datetime.min.replace(tzinfo=timezone.utc),
                                        timestamp(r.get("last_failure_at")) or datetime.min.replace(tzinfo=timezone.utc)), default={})
        configured = configs.get(provider, row.get("configured"))
        failed, succeeded = timestamp(row.get("last_failure_at")), timestamp(row.get("last_success_at"))
        circuit = timestamp(row.get("circuit_open_until"))
        if row.get("enabled") is False:
            state, reason = "disabled", "capability disabled in registry"
        elif configured is False:
            state, reason = "unconfigured", "provider is not configured in this runtime"
        elif circuit and circuit > now:
            state, reason = "circuit_open", "provider circuit is open"
        elif failed and (not succeeded or failed >= succeeded) and (not stamp or failed >= stamp):
            state, reason = "degraded", "a newer provider operation failed"
        requested, received = obs["requested"], obs["received"]
        items.append({"source_key": key, "provider_key": provider, "capability": capability, "label": label,
            "state": state, "reason": reason, "last_observation_state": historical_state,
            "configured": configured, "scope": scope, "requested": requested, "received": received,
            "valid_symbols": obs["valid"], "coverage_ratio": round(min(1, received/requested), 4) if requested and received is not None else None,
            "evidence_observed_at": stamp, "age_seconds": round(age, 1) if age is not None else None,
            "freshness_budget_seconds": budget, "evidence_limit": obs["evidence_limit"],
            "current_limit": limits.get("longhu_quote") if key == "longhuvip" else None,
            "last_success_at": succeeded, "last_failure_at": failed,
            "last_latency_ms": obs["latency_ms"] if obs["latency_ms"] is not None else row.get("last_latency_ms"),
            "last_row_count": row.get("last_row_count"), "error_present": obs["error_present"] or bool(row.get("last_error")),
            "decision_eligible": False})
    return {"observed_at": now, "latest_scan_observed_at": scan.get("observed_at"),
            "session_active": session_active, "timezone": "Asia/Shanghai", "items": items,
            "summary": {state: sum(r["state"] == state for r in items) for state in STATES},
            "runtime_limits": limits, "evidence_mode": "stored_observations", "live_effect": "none",
            "policy": "Health is evidence only; configuration belongs to the queried runtime; no upstream calls on GET."}


def continuous_session(now: datetime, calendar_open: bool | None) -> bool | None:
    local = now.astimezone(CN)
    clock_open = local.weekday() < 5 and (time(9, 30) <= local.time() < time(11, 30) or time(13) <= local.time() < time(15))
    return False if not clock_open else calendar_open
