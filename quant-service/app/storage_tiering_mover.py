"""Asynchronous hot -> cold copy with overlap, then verified hot deletion.

Executes ``storage_tiering_policy.RULES`` from the research scheduler over
the 5433 batch lane, never during 09:00-15:45 on a trading day:

* copy - every closed session still in the hot table is copied into the
  owner's cold twin (``stock_cold`` tablespace) in bounded chunks, skipping
  rows the twin already holds.  The overlap starts the evening a session
  closes;
* prune - once a session is older than the rule's ``hot_sessions``, hot rows
  are deleted only where the cold twin holds the same key *and* the same
  verification columns (for raw observations the payload SHA-256).

A rule runs only when this role may read and insert the cold twin, may
delete from the hot table, the twin has every key/verification column, and
an index leads with the key (otherwise each anti-join would scan the whole
twin).  Anything missing is reported (``awaiting_owner_grant``,
``schema_mismatch``, ``cold_twin_needs_key_index``) and nothing is touched.
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .storage_tiering_policy import RULES, TierRule

_CN = ZoneInfo("Asia/Shanghai")
MOVER_VERSION = "peer-storage-tiering-mover-v1"
RUN_CAPABILITY = "storage_tiering_run"
SYMBOLS_PER_CHUNK = 50
STATEMENT_TIMEOUT = "120s"


@dataclass(frozen=True)
class TableSpec:
    key: str
    verify: tuple[str, ...]
    per_symbol_index: bool          # an index leads with symbol (per-symbol probes are cheap)


TABLES = {
    "quant.raw_market_observations": TableSpec("observation_id", ("payload_sha256",), True),
    "quant.intraday_quote_observations": TableSpec("quote_observation_id", ("observed_at", "raw"), True),
    "quant.intraday_rule_input_snapshots": TableSpec("rule_input_snapshot_id", ("input_hash",), False),
}


def transfer_allowed(now: datetime, trading_day: bool) -> bool:
    """Never while a session can be read live: 09:00-15:45 on a trading day."""
    local = now.astimezone(_CN).time()
    return not (trading_day and time(9, 0) <= local < time(15, 45))


def _split(name: str) -> tuple[str, str]:
    schema, table = name.split(".", 1)
    return schema, table


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0), tzinfo=_CN)
    return start, start + timedelta(days=1)


def rule_readiness(connection: Any, rule: TierRule) -> dict[str, Any]:
    """Can this rule run?  Privileges, shared columns and a key index on the cold twin."""
    spec = TABLES.get(rule.table)
    if spec is None:
        return {"status": "unsupported_table"}
    row = connection.execute(
        """SELECT to_regclass(%s) IS NOT NULL AS cold_exists,
                  CASE WHEN to_regclass(%s) IS NULL THEN false ELSE
                       has_table_privilege(%s,'SELECT') AND has_table_privilege(%s,'INSERT') END AS cold_ok,
                  has_table_privilege(%s,'SELECT') AND has_table_privilege(%s,'DELETE') AS hot_ok""",
        (rule.cold_twin, rule.cold_twin, rule.cold_twin, rule.cold_twin, rule.table, rule.table)).fetchone()
    if not row["cold_exists"]:
        return {"status": "cold_twin_missing"}
    if not row["cold_ok"] or not row["hot_ok"]:
        return {"status": "awaiting_owner_grant",
                "needs": [item for item, ok in (("SELECT,INSERT on " + rule.cold_twin, row["cold_ok"]),
                                                ("SELECT,DELETE on " + rule.table, row["hot_ok"])) if not ok]}
    columns: dict[str, list[str]] = {}
    for name in (rule.table, rule.cold_twin):
        schema, table = _split(name)
        columns[name] = [r["column_name"] for r in connection.execute(
            """SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s
                ORDER BY ordinal_position""", (schema, table)).fetchall()]
    shared = [column for column in columns[rule.table] if column in set(columns[rule.cold_twin])]
    required = {spec.key, rule.time_column, "symbol", *spec.verify, *(("capability",) if rule.capability else ())}
    missing = sorted(required - set(shared))
    if missing:
        return {"status": "schema_mismatch", "missing_in_cold_twin": missing}
    schema, table = _split(rule.cold_twin)
    indexes = [r["indexdef"] for r in connection.execute(
        "SELECT indexdef FROM pg_indexes WHERE schemaname=%s AND tablename=%s", (schema, table)).fetchall()]
    if not any(f"({spec.key}" in definition.replace('"', "") for definition in indexes):
        return {"status": "cold_twin_needs_key_index", "key": spec.key}
    return {"status": "ready", "columns": shared, "spec": spec}


def _symbols(connection: Any, rule: TierRule, before: datetime) -> list[str | None]:
    if rule.capability:
        rows = connection.execute(
            """WITH RECURSIVE s AS (
                   SELECT min(symbol) AS symbol FROM quant.raw_market_observations WHERE capability=%s
                   UNION ALL
                   SELECT (SELECT min(symbol) FROM quant.raw_market_observations WHERE capability=%s AND symbol>s.symbol)
                     FROM s WHERE s.symbol IS NOT NULL)
               SELECT symbol FROM s WHERE symbol IS NOT NULL""", (rule.capability, rule.capability)).fetchall()
        symbols: list[str | None] = [row["symbol"] for row in rows]
        null = connection.execute(
            """SELECT 1 FROM quant.raw_market_observations WHERE capability=%s AND symbol IS NULL
                AND effective_at<%s LIMIT 1""", (rule.capability, before)).fetchone()
        return symbols + ([None] if null else [])
    rows = connection.execute(
        f"SELECT DISTINCT symbol FROM {rule.table} WHERE {rule.time_column}<%s", (before,)).fetchall()
    return [row["symbol"] for row in rows]


def _selector(rule: TierRule, symbols: list[str | None]) -> tuple[str, list[Any]]:
    """WHERE fragment (hot alias h) for one symbol chunk of one rule."""
    parts, params = [], []
    if rule.capability:
        parts.append("h.capability=%s")
        params.append(rule.capability)
    named = [symbol for symbol in symbols if symbol is not None]
    if named and None in symbols:
        parts.append("(h.symbol=ANY(%s) OR h.symbol IS NULL)")
        params.append(named)
    elif named:
        parts.append("h.symbol=ANY(%s)")
        params.append(named)
    else:
        parts.append("h.symbol IS NULL")
    return " AND ".join(parts), params


def _oldest(connection: Any, rule: TierRule, symbols: list[str | None], spec: TableSpec) -> datetime | None:
    named = [symbol for symbol in symbols if symbol is not None]
    if spec.per_symbol_index and named:
        capability = "AND capability=%s" if rule.capability else ""
        params: list[Any] = [named, *([rule.capability] if rule.capability else [])]
        row = connection.execute(
            f"""SELECT min(x.m) AS oldest FROM unnest(%s::text[]) s(symbol)
                  CROSS JOIN LATERAL (SELECT min({rule.time_column}) AS m FROM {rule.table}
                                       WHERE symbol=s.symbol {capability}) x""", params).fetchone()
        oldest = row["oldest"] if row else None
    else:
        capability = "WHERE capability=%s" if rule.capability else ""
        row = connection.execute(f"SELECT min({rule.time_column}) AS oldest FROM {rule.table} {capability}",
                                 [rule.capability] if rule.capability else []).fetchone()
        oldest = row["oldest"] if row else None
    return oldest


def _copy_sql(rule: TierRule, columns: list[str], spec: TableSpec, where: str) -> str:
    listed = ",".join(columns)
    return (f"INSERT INTO {rule.cold_twin}({listed}) SELECT {','.join('h.' + c for c in columns)} FROM {rule.table} h "
            f"WHERE {where} AND h.{rule.time_column}>=%s AND h.{rule.time_column}<%s "
            f"AND NOT EXISTS (SELECT 1 FROM {rule.cold_twin} c WHERE c.{spec.key}=h.{spec.key}) ON CONFLICT DO NOTHING")


def _delete_sql(rule: TierRule, spec: TableSpec, where: str) -> str:
    verify = " AND ".join(f"c.{column} IS NOT DISTINCT FROM h.{column}" for column in spec.verify)
    return (f"DELETE FROM {rule.table} h WHERE {where} AND h.{rule.time_column}>=%s AND h.{rule.time_column}<%s "
            f"AND EXISTS (SELECT 1 FROM {rule.cold_twin} c WHERE c.{spec.key}=h.{spec.key} AND {verify})")


class StorageTieringMover:
    """Bounded passes; remembers fully copied (rule, day) pairs within the process."""

    def __init__(self, database: Any, *, now: Callable[[], datetime] | None = None,
                 clock: Callable[[], float] = _time.monotonic) -> None:
        self.database = database
        self.now = now or (lambda: datetime.now(_CN))
        self.clock = clock
        self._copied: set[tuple[str, str, date]] = set()

    def _execute(self, sql: str, params: list[Any] | tuple[Any, ...]) -> int:
        with self.database.transaction() as connection:
            connection.execute(f"SET LOCAL statement_timeout='{STATEMENT_TIMEOUT}'")
            cursor = connection.execute(sql, params)
            return int(getattr(cursor, "rowcount", 0) or 0)

    def run_pass(self, *, budget_seconds: float = 480.0) -> dict[str, Any]:
        started, now = self.clock(), self.now()
        today = now.astimezone(_CN).date()
        with self.database.transaction() as connection:
            trading_today = bool(connection.execute(
                """SELECT 1 FROM quant.market_trade_calendar WHERE exchange='SSE' AND is_open AND calendar_date=%s""",
                (today,)).fetchone())
            sessions = [row["calendar_date"] for row in connection.execute(
                """SELECT calendar_date FROM quant.market_trade_calendar WHERE exchange='SSE' AND is_open
                    AND calendar_date<%s ORDER BY calendar_date DESC LIMIT 400""", (today,)).fetchall()]
            readiness = {self._rule_key(rule): rule_readiness(connection, rule) for rule in RULES}
        report: dict[str, Any] = {"version": MOVER_VERSION, "at": now.isoformat(), "rules": {}, "copied_rows": 0,
                                  "deleted_rows": 0, "complete": True}
        if not transfer_allowed(now, trading_today):
            report.update({"status": "outside_transfer_window", "complete": False})
            return report
        today_start, _ = _day_bounds(today)
        for rule in RULES:
            key = self._rule_key(rule)
            ready = readiness[key]
            entry: dict[str, Any] = {"status": ready["status"],
                                     **{k: v for k, v in ready.items() if k not in ("status", "columns", "spec")}}
            report["rules"][key] = entry
            if ready["status"] != "ready":
                continue
            keep = sessions[:max(0, rule.hot_sessions - 1)]
            prune_before = min(keep) if keep else today
            spec, columns = ready["spec"], ready["columns"]
            with self.database.transaction() as connection:
                connection.execute(f"SET LOCAL statement_timeout='{STATEMENT_TIMEOUT}'")
                symbols = _symbols(connection, rule, today_start)
                oldest = _oldest(connection, rule, symbols, spec) if symbols else None
            entry.update({"copied": 0, "deleted": 0, "prune_before": prune_before.isoformat(),
                          "oldest_hot": oldest.isoformat() if oldest else None})
            if oldest is None:
                continue
            chunks = [symbols[i:i + SYMBOLS_PER_CHUNK] for i in range(0, len(symbols), SYMBOLS_PER_CHUNK)]
            day = oldest.astimezone(_CN).date()
            while day < today:
                if self.clock() - started > budget_seconds or not transfer_allowed(self.now(), trading_today):
                    report["complete"] = False
                    entry["stopped_at_day"] = day.isoformat()
                    break
                lower, upper = _day_bounds(day)
                if (key, rule.table, day) not in self._copied:
                    for chunk in chunks:
                        where, params = _selector(rule, chunk)
                        entry["copied"] += self._execute(_copy_sql(rule, columns, spec, where), [*params, lower, upper])
                    self._copied.add((key, rule.table, day))
                if day < prune_before:
                    for chunk in chunks:
                        where, params = _selector(rule, chunk)
                        entry["deleted"] += self._execute(_delete_sql(rule, spec, where), [*params, lower, upper])
                day += timedelta(days=1)
            report["copied_rows"] += entry["copied"]
            report["deleted_rows"] += entry["deleted"]
            if not report["complete"]:
                break
        statuses = {entry["status"] for entry in report["rules"].values()}
        report["status"] = ("completed" if statuses == {"ready"} and report["complete"]
                            else "partial" if "ready" in statuses else sorted(statuses)[0] if len(statuses) == 1
                            else "not_ready")
        report["elapsed_seconds"] = round(self.clock() - started, 1)
        return report

    @staticmethod
    def _rule_key(rule: TierRule) -> str:
        return f"{rule.table}:{rule.capability}" if rule.capability else rule.table


def persist_run_report(database: Any, report: dict[str, Any]) -> None:
    import hashlib
    import json

    from psycopg.types.json import Json
    body = json.loads(json.dumps(report, default=str))
    serialized = json.dumps(body, sort_keys=True)
    at = datetime.fromisoformat(body["at"])
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO quant.raw_market_observations(provider_key,capability,market,symbol,effective_at,available_at,payload_sha256,normalized,payload)
               VALUES('quant_scan',%s,'cn','tiering:run',%s,%s,%s,%s,%s)
               ON CONFLICT(provider_key,capability,market,symbol,effective_at,payload_sha256) DO NOTHING""",
            (RUN_CAPABILITY, at, at, hashlib.sha256(serialized.encode()).hexdigest(),
             Json({"status": body.get("status"), "copied_rows": body.get("copied_rows"), "deleted_rows": body.get("deleted_rows")}),
             Json(body)))


def latest_run_report(connection: Any) -> dict[str, Any] | None:
    row = connection.execute(
        """SELECT payload FROM quant.raw_market_observations WHERE capability=%s AND symbol='tiering:run'
            ORDER BY effective_at DESC LIMIT 1""", (RUN_CAPABILITY,)).fetchone()
    return dict(row["payload"]) if row else None


__all__ = ["MOVER_VERSION", "RUN_CAPABILITY", "StorageTieringMover", "TABLES", "latest_run_report",
           "persist_run_report", "rule_readiness", "transfer_allowed"]
