"""Two read-only boards: every data source with its capabilities and health, and
every registered strategy with the health of the data it needs.

The provider health table records each physical call by provider key and
capability. Read alone it cannot say which part of the data plane a failure
touches, nor whether a source is retired, and "no failures" passes for
healthy even when the last success was weeks ago. The data-source board
joins that table to the catalog:
- which capabilities each source serves, at what priority and status;
- its lifecycle: active, dormant, retired, or uncatalogued;
- a verdict that also weighs staleness against the source's cadence.

The strategy board starts from what each strategy declares it needs
(platform.strategy_data_needs). For each need it names the catalog source that
serves it and that source's verdict, so a strategy whose inputs are failing
is visible before its output is trusted.

Research evidence only. Neither board probes a provider or changes a strategy.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .datasources.catalog import CAPABILITIES, HEALTH_CAPABILITY_ALIASES, SOURCES, bindings_for, capabilities_of, health_capability
from .datasources.contracts import DECLARED, DORMANT, LIVE_VERIFIED, RETIRED, UNSUPPORTED
from .platform.strategy_data_needs import STRATEGY_DATA_NEEDS
from .platform.strategy_registry import STRATEGY_CONTRACTS
from .strategy_cards_read_model import LINES

CN_TZ = ZoneInfo("Asia/Shanghai")
BOARD_VERSION = "research-boards-v1"
#: Worst first.
VERDICTS = ("circuit_open", "failing", "stale", "never_succeeded", "degraded", "unmonitored", "healthy",
            "dormant", "retired")
#: Grains whose sources run during the session and should succeed every few minutes then.
INTRADAY_GRAINS = {"intraday", "realtime"}
INTRADAY_STALE = timedelta(minutes=15)
#: Covers a weekend; a longer holiday shows as stale, which it is.
DAILY_STALE = timedelta(days=3)
FAILING_AT = 3
SESSION = ((time(9, 30), time(11, 30)), (time(13, 0), time(15, 0)))


def _in_session(moment: datetime) -> bool:
    local = moment.astimezone(CN_TZ)
    return local.weekday() < 5 and any(start <= local.time() <= end for start, end in SESSION)


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)


def health_rows(connection: Any) -> dict[str, list[dict[str, Any]]]:
    rows = connection.execute(
        """SELECT provider_key,capability,market,consecutive_failures,last_success_at,last_failure_at,
                  left(coalesce(last_error,''),240) AS last_error,last_latency_ms,last_row_count,circuit_open_until
             FROM quant.provider_health ORDER BY provider_key,capability""").fetchall()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in (dict(item) for item in rows):
        grouped[str(row["provider_key"])].append(row)
    return grouped


def _lifecycle(statuses: set[str]) -> str:
    if not statuses:
        return "uncatalogued"
    if statuses <= {RETIRED}:
        return "retired"
    if statuses <= {DORMANT, UNSUPPORTED, RETIRED}:
        return "dormant"
    return "active"


def source_verdict(lifecycle: str, grains: set[str], rows: list[dict[str, Any]], now: datetime) -> tuple[str, list[str]]:
    """One verdict for a source, and the reasons behind it."""
    if lifecycle == "retired":
        return "retired", ["目录中已全部退役，不再告警"]
    if lifecycle == "dormant":
        return "dormant", ["目录中没有在用的绑定"]
    if not rows:
        return "unmonitored", ["没有任何健康记录（可能经其他提供方键记录，或尚未运行）"]
    reasons: list[str] = []
    open_circuits = [row["capability"] for row in rows
                     if row["circuit_open_until"] is not None and row["circuit_open_until"] > now]
    if open_circuits:
        return "circuit_open", [f"熔断中：{'、'.join(open_circuits)}"]
    failing = [row["capability"] for row in rows if (row["consecutive_failures"] or 0) >= FAILING_AT]
    if failing:
        return "failing", [f"连续失败≥{FAILING_AT}：{'、'.join(failing)}"]
    successes = [row["last_success_at"] for row in rows if row["last_success_at"] is not None]
    if not successes:
        return "never_succeeded", ["有健康记录但从未成功"]
    latest = max(successes)
    intraday = bool(grains & INTRADAY_GRAINS)
    limit = INTRADAY_STALE if intraday and _in_session(now) else DAILY_STALE
    if now - latest > limit:
        reasons.append(f"最近一次成功距今 {round((now - latest).total_seconds() / 3600, 1)} 小时，超过"
                       f"{'盘中 15 分钟' if limit == INTRADAY_STALE else ' 3 天'}")
        return "stale", reasons
    degraded = [row["capability"] for row in rows if (row["consecutive_failures"] or 0) > 0]
    if degraded:
        return "degraded", [f"最近有失败：{'、'.join(degraded)}"]
    return "healthy", reasons


def _source_order(bindings: list[Any]) -> int:
    active = [binding.priority for binding in bindings if binding.status in (LIVE_VERIFIED, DECLARED)]
    return min(active) if active else 1000 + min((binding.priority for binding in bindings), default=0)


def datasource_board(connection: Any, now: datetime) -> dict[str, Any]:
    grouped = health_rows(connection)
    sources = []
    for key, source in SOURCES.items():
        bindings = capabilities_of(key)
        lifecycle = _lifecycle({binding.status for binding in bindings})
        grains = {CAPABILITIES[binding.capability].grain for binding in bindings
                  if binding.capability in CAPABILITIES and binding.status in (LIVE_VERIFIED, DECLARED)}
        rows = grouped.pop(key, [])
        verdict, reasons = source_verdict(lifecycle, grains, rows, now)
        successes = [row["last_success_at"] for row in rows if row["last_success_at"] is not None]
        sources.append({
            "key": key, "label": source.label, "upstream": source.upstream, "license": source.license,
            "protocol": source.protocol, "cost": source.cost, "risks": source.risks, "order": _source_order(bindings),
            "lifecycle": lifecycle, "verdict": verdict, "reasons": reasons,
            "last_success_at": _iso(max(successes)) if successes else None,
            "capabilities": [{
                "capability": binding.capability,
                "label": CAPABILITIES[binding.capability].label if binding.capability in CAPABILITIES else binding.capability,
                "grain": CAPABILITIES[binding.capability].grain if binding.capability in CAPABILITIES else None,
                "priority": binding.priority, "status": binding.status, "store": binding.store,
                "decision_eligible": binding.decision_eligible, "notes": binding.notes,
            } for binding in sorted(bindings, key=lambda item: (item.priority, item.capability))],
            "health": [_health_item(row, now) for row in rows],
        })
    for key, rows in grouped.items():
        verdict, reasons = source_verdict("uncatalogued", set(), rows, now)
        sources.append({"key": key, "label": key, "order": 2000, "lifecycle": "uncatalogued", "verdict": verdict,
                        "reasons": reasons + ["不在数据源目录中"], "capabilities": [],
                        "health": [_health_item(row, now) for row in rows],
                        "last_success_at": _iso(max((row["last_success_at"] for row in rows
                                                     if row["last_success_at"] is not None), default=None))})
    # Priority first (Longhu leads); at equal priority the source serving more capabilities.
    sources.sort(key=lambda item: (VERDICTS.index(item["verdict"]) >= VERDICTS.index("dormant"), item["order"],
                                   -sum(cap["status"] in (LIVE_VERIFIED, DECLARED) for cap in item["capabilities"]),
                                   item["key"]))
    summary: dict[str, int] = defaultdict(int)
    for item in sources:
        summary[item["verdict"]] += 1
    return {
        "observed_at": now.isoformat(), "board_version": BOARD_VERSION, "in_session": _in_session(now),
        "summary": dict(summary), "sources": sources,
        "alerts": [{"key": item["key"], "label": item["label"], "verdict": item["verdict"], "reasons": item["reasons"]}
                   for item in sources if item["verdict"] in {"circuit_open", "failing", "stale", "never_succeeded"}],
        "definitions": {
            "order": "在用绑定的最高优先级（数字越小越优先；开盘啦优先）",
            "stale": "盘中实时类来源 15 分钟内无成功、其他来源 3 天内无成功",
            "failing": f"某项能力连续失败≥{FAILING_AT} 次", "degraded": "某项能力最近失败过但未达失败阈值",
            "retired": "目录中全部绑定已退役，健康记录只作历史，不告警",
            "unmonitored": "目录在用但没有健康记录",
        },
        "research_only": True, "live_effect": "none",
    }


def _health_item(row: dict[str, Any], now: datetime) -> dict[str, Any]:
    circuit = row["circuit_open_until"]
    return {
        "capability": row["capability"], "market": row["market"], "consecutive_failures": row["consecutive_failures"],
        "last_success_at": _iso(row["last_success_at"]), "last_failure_at": _iso(row["last_failure_at"]),
        "last_error": row["last_error"] or None, "last_latency_ms": row["last_latency_ms"],
        "last_row_count": row["last_row_count"], "circuit_open": bool(circuit and circuit > now),
        "circuit_open_until": _iso(circuit),
    }


def _parse(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def _ledger_days(connection: Any, since: date) -> dict[str, dict[str, Any]]:
    rows = connection.execute(
        """SELECT DISTINCT ON (strategy_key) strategy_key,as_of_date,count(*) OVER (PARTITION BY strategy_key,as_of_date) AS n
             FROM quant.strategy_daily_candidates WHERE as_of_date>=%s ORDER BY strategy_key,as_of_date DESC""",
        (since,)).fetchall()
    return {dict(row)["strategy_key"]: {"as_of_date": _iso(dict(row)["as_of_date"]), "candidates": dict(row)["n"]}
            for row in rows}


#: A strategy whose required input sits here cannot be trusted now.
BLOCKING = {"circuit_open", "failing", "stale", "never_succeeded", "retired", "dormant", "unserved"}


def _primary(capability: str) -> tuple[Any | None, list[Any]]:
    """The binding a capability is read from now: live-verified first, then declared, by priority."""
    serving = [binding for binding in bindings_for(capability) if binding.status in (LIVE_VERIFIED, DECLARED)]
    serving.sort(key=lambda binding: (binding.status != LIVE_VERIFIED, binding.priority, binding.source))
    return (serving[0] if serving else None), serving[1:]


def need_verdict(source: str, capability: str, rows: list[dict[str, Any]], source_verdict_value: str,
                 now: datetime) -> str:
    """How healthy one capability of one source is, not the source as a whole.

    A source's own row for the capability decides when there is one. Otherwise
    the source verdict stands, unless every troubled row of the source
    belongs to another catalog capability: then this capability is simply not
    monitored on its own (腾讯's order-book failures say nothing about its
    published limit prices).
    """
    physical = health_capability(source, capability)
    own = [row for row in rows if row["capability"] == physical]
    if own:
        return source_verdict("active", {CAPABILITIES[capability].grain} if capability in CAPABILITIES else set(), own, now)[0]
    if source_verdict_value in BLOCKING - {"retired", "dormant", "unserved"}:
        others = {alias for (alias_source, alias_capability), alias in HEALTH_CAPABILITY_ALIASES.items()
                  if alias_source == source and alias_capability != capability}
        troubled = [row for row in rows if (row["consecutive_failures"] or 0) > 0
                    or (row["circuit_open_until"] is not None and row["circuit_open_until"] > now)]
        if troubled and all(row["capability"] in others for row in troubled):
            return "unmonitored"
    return source_verdict_value


def strategy_board(connection: Any, now: datetime) -> dict[str, Any]:
    board = datasource_board(connection, now)
    verdicts = {item["key"]: item["verdict"] for item in board["sources"]}
    raw_rows = {item["key"]: [{**row, "last_success_at": _parse(row["last_success_at"]),
                               "circuit_open_until": _parse(row["circuit_open_until"])} for row in item["health"]]
                for item in board["sources"]}
    ledger = _ledger_days(connection, now.astimezone(CN_TZ).date() - timedelta(days=45))
    strategies = []
    for key, contract in STRATEGY_CONTRACTS.items():
        needs = STRATEGY_DATA_NEEDS.get(key)
        inputs = []
        for need in needs.needs if needs else ():
            primary, rest = _primary(need.capability)
            inputs.append({
                "capability": need.capability, "label": CAPABILITIES[need.capability].label if need.capability in CAPABILITIES else need.capability,
                "required": need.required, "purpose": need.purpose, "taxonomies": list(need.taxonomies),
                "source": primary.source if primary else None, "binding_status": primary.status if primary else None,
                "source_verdict": need_verdict(primary.source, need.capability, raw_rows.get(primary.source, []),
                                               verdicts.get(primary.source, "unmonitored"), now) if primary else "unserved",
                "fallbacks": [{"source": binding.source, "status": binding.status, "verdict": verdicts.get(binding.source)}
                              for binding in rest[:2]],
            })
        required = [item for item in inputs if item["required"]]
        blocked = [item for item in required if item["source_verdict"] in BLOCKING]
        weak = [item for item in required if item["source_verdict"] not in BLOCKING and item["source_verdict"] != "healthy"]
        readiness = "no_declared_needs" if not inputs else "blocked" if blocked else "degraded" if weak else "ready"
        lines = [line for line, (_name, contract_key, _style) in LINES.items() if contract_key == key]
        strategies.append({
            "key": key, "model_version": contract.model_version, "maturity": contract.maturity,
            "live_effect": contract.live_effect, "alert_effect": contract.alert_effect,
            "deprecated": contract.deprecated_reason is not None, "description": contract.description,
            "readiness": readiness,
            "blocking_inputs": [f"{item['capability']}←{item['source']}（{item['source_verdict']}）" for item in blocked],
            "weak_inputs": [f"{item['capability']}←{item['source']}（{item['source_verdict']}）" for item in weak],
            "inputs": inputs, "ledger_lines": {line: ledger.get(line) for line in lines},
        })
    order = {"blocked": 0, "degraded": 1, "ready": 2, "no_declared_needs": 3}
    strategies.sort(key=lambda item: (item["deprecated"], order[item["readiness"]], item["key"]))
    summary: dict[str, int] = defaultdict(int)
    for item in strategies:
        summary[item["readiness"]] += 1
    return {
        "observed_at": now.isoformat(), "board_version": BOARD_VERSION, "summary": dict(summary),
        "strategies": strategies,
        "definitions": {
            "readiness": "ready：所有必需输入的首选来源健康；degraded：有必需输入的来源降级或无监控；"
                         "blocked：有必需输入的来源熔断/失败/陈旧/从未成功/已退役或无来源",
            "source": "目录中该能力在用（live_verified/declared）的最高优先级来源",
            "ledger_lines": "该策略在日候选台账中的线，及最近一个台账日的候选数",
        },
        "research_only": True, "live_effect": "none",
    }


__all__ = ["BOARD_VERSION", "VERDICTS", "datasource_board", "source_verdict", "strategy_board"]
