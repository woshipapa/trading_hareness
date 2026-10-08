"""The 小杰 leader-flow research pass that rides each watch scan's all-A cross-section.

Moved out of main.py (docs/decisions/0008) with the per-session state it owns:
the session reference (trade limits, memberships, prior bars), the MA5-break
and launch-velocity timers, and a briefly cached board-flow point. Research
only throughout: its signal events carry their own stage, its alerts say they
carry zero live weight, and the strategy stays at zero weight in the
promotion registry.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from time import monotonic
from typing import Any
import uuid
from zoneinfo import ZoneInfo

from psycopg.types.json import Json

from .launch_radar import evaluate_launch_radar, record_launch_observations as record_launch_radar_observations
from .xiaojie_alerts import alert_text
from .xiaojie_indicators import evaluate_pool as evaluate_xiaojie_leader_pool
from .xiaojie_indicators import leader_pool as leader_pool_symbols
from .xiaojie_leader_flow import MODEL_VERSION as XIAOJIE_LEADER_FLOW_MODEL_VERSION
from .xiaojie_leader_flow import alert_priority as xiaojie_alert_priority
from .xiaojie_leader_flow import research_alert_allowed as xiaojie_research_alert_allowed
from .xiaojie_observation_repository import (
    alerted_count as xiaojie_alerted_count, mark_alerted as mark_xiaojie_alerted,
    record_candidates as record_xiaojie_candidates,
    unalerted_research_candidates as read_unalerted_xiaojie_candidates,
)
from .xiaojie_reference_repository import (
    ensure_session_trade_limits as ensure_xiaojie_session_trade_limits,
    latest_board_flow as read_xiaojie_latest_board_flow,
    load_session_reference as load_xiaojie_session_reference,
    persist_trade_limit_rows as persist_xiaojie_trade_limit_rows,
    trade_limits as read_xiaojie_trade_limits,
)

#: Alerts are per newly-appearing (symbol, mode); this bounds a pathological
#: day.  The running tally is read from the observations table, not held here,
#: so a restart cannot reset it.
MAX_ALERTS_PER_SCAN = 5
MAX_ALERTS_PER_SESSION = 40
#: The board-flow loop stores one point a minute; the 30-second xiaojie scan
#: rereads it at most this often.
BOARD_FLOW_REFRESH_SECONDS = 30.0


@dataclass(frozen=True)
class XiaojieLeaderFlowDependencies:
    run_database: Callable[..., Awaitable[Any]]
    with_connection: Callable[[Callable[[Any], Any]], Any]
    fetch_limit_cross_section: Callable[[date], Awaitable[tuple[list[dict[str, Any]], str]]]
    refresh_confluence: Callable[[date, datetime, list[dict[str, Any]]], Awaitable[None]]
    teacher_plan: Callable[[date, str], Mapping[str, Any] | None]
    chat_context: Callable[[str, datetime], Awaitable[str | None]]
    deliver_alert: Callable[[Any, str], Awaitable[Any]]
    safe_error: Callable[[str, int], str]


def persist_signal_event(connection: Any, scan_id: uuid.UUID, observed_at: datetime,
                                  candidate: dict[str, Any]) -> uuid.UUID:
    """Record a research observation as a distinctly-staged signal event.

    ``stage`` isolates it: every decision-path consumer selects on the stages
    the watchlist scan emits, so a research row cannot be mistaken for one.
    """
    event_id = uuid.uuid4()
    mode = str(candidate.get("mode") or "unclassified")
    connection.execute(
        """INSERT INTO quant.intraday_signal_events(
                signal_event_id,scan_id,symbol,signal_key,signal_type,severity,state,score,
                observed_at,conditions,evidence,risk_flags,stage)
           VALUES(%s,%s,%s,%s,'watch','info','alerted',0,%s,%s,%s,%s,'xiaojie_leader_flow_research')""",
        (event_id, scan_id, candidate["symbol"], f"{candidate['symbol']}:xiaojie:{mode}",
         observed_at, Json({"mode": mode, "position": candidate.get("position") or {},
                            "stop_loss": candidate.get("stop_loss") or {}}),
         Json(candidate.get("evidence") or {}), Json(candidate.get("risk_flags") or [])),
    )
    return event_id


class XiaojieLeaderFlowRuntime:
    def __init__(self, deps: XiaojieLeaderFlowDependencies) -> None:
        self.deps = deps
        #: One session's reference is reloaded only when the trading date rolls over.
        self.session_reference: dict[str, Any] = {"trading_date": None, "reference": None}
        #: Per-session MA5-break timers, reset when the trading date rolls over.
        self.ma5_break_state: dict[str, Any] = {}
        self.launch_velocity_state: dict[str, Any] = {}
        self.board_flow: dict[str, Any] = {"trading_date": None, "read_at": 0.0, "value": None}

    async def session_context(self, trading_date: date) -> dict[str, Any]:
        """Load and cache one session's limits, memberships and prior-bar reference."""
        cached = self.session_reference
        if cached["trading_date"] == trading_date and cached["reference"] is not None:
            return cached["reference"]

        async def read_limits(day: date) -> dict[str, float]:
            return await self.deps.run_database(
                lambda: self.deps.with_connection(lambda connection: read_xiaojie_trade_limits(connection, day)),
            )

        async def persist_limits(day: date, rows: list[dict[str, Any]]) -> int:
            return await self.deps.run_database(
                lambda: self.deps.with_connection(lambda connection: persist_xiaojie_trade_limit_rows(
                    connection, day, rows, "tushare", datetime.now(timezone.utc))),
                timeout_seconds=180,
            )

        # Limit prices are published pre-open but only land in the table after the
        # close, so intraday they must be provisioned before anything reads them.
        await ensure_xiaojie_session_trade_limits(
            trading_date, read_limits=read_limits,
            fetch_limit_cross_section=lambda day: self.deps.fetch_limit_cross_section(day),
            persist_limits=persist_limits,
        )
        reference = await self.deps.run_database(
            lambda: self.deps.with_connection(lambda connection: loadself.session_reference(connection, trading_date)),
            timeout_seconds=180,
        )
        self.session_reference.update({"trading_date": trading_date, "reference": reference})
        self.ma5_break_state.clear()
        self.launch_velocity_state.clear()
        return reference

    async def board_flow_point(self, trading_date: date, observed_at: datetime) -> dict[str, Any]:
        """The newest stored licensed board-flow point, cached briefly per process.

        A failed read leaves the sector inputs absent for this scan; it never
        stops the leader-flow pass.
        """
        cached = self.board_flow
        now = monotonic()
        if (cached["value"] is not None and cached["trading_date"] == trading_date
                and now - cached["read_at"] < BOARD_FLOW_REFRESH_SECONDS):
            return cached["value"]
        try:
            value = await self.deps.run_database(
                lambda: self.deps.with_connection(lambda connection: read_xiaojie_latest_board_flow(
                    connection, trading_date, observed_at)),
                timeout_seconds=15,
            )
        except Exception as error:  # noqa: BLE001 - sector inputs are optional per scan
            return {"status": "unavailable", "reason": self.deps.safe_error(str(error), 160), "boards": {}}
        cached.update({"trading_date": trading_date, "read_at": now, "value": value})
        return value

    async def run(self, *, scan_id: uuid.UUID, observed_at: datetime,
                                      all_a_rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Evaluate the leader pool from this scan's own cross-section.

        Research-only throughout: the emitted signal events carry a dedicated
        stage so nothing on the decision path can mistake them for watchlist
        alerts, and the strategy stays at zero live weight in the promotion
        registry.
        """
        if not all_a_rows:
            return {"status": "skipped", "reason": "no all-A cross-section in this scan"}
        trading_date = observed_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
        reference = await self.session_context(trading_date)
        if not reference.get("limits"):
            return {"status": "blocked", "reason": "session trade limits unavailable"}
        board_flow = await self.board_flow_point(trading_date, observed_at)
        result = evaluate_xiaojie_leader_pool(
            all_a_rows, limits=reference["limits"], membership=reference["membership"],
            references=reference["references"], observed_at=observed_at,
            ma5_break_state=self.ma5_break_state,
            market_volume_baseline=reference.get("market_volume_baseline"),
            sector_flow=board_flow, membership_taxonomy=reference.get("membership_taxonomy"),
        )
        candidates = result["candidates"]
        await self.deps.refresh_confluence(trading_date, observed_at, candidates)
        fresh = await self.deps.run_database(
            lambda: self.deps.with_connection(lambda connection: record_xiaojie_candidates(
                connection, trading_date, observed_at, scan_id, candidates)),
            timeout_seconds=60,
        ) if candidates else []
        new_candidate_count = len(fresh)

        # A policy widening must also reach candidates recorded by an earlier
        # scan. Only the explicit sealed 潜龙出海 research mode is reread, and
        # alerted_at makes this idempotent across the 30-second scan loop.
        pending_qianlong = await self.deps.run_database(
            lambda: self.deps.with_connection(lambda connection: read_unalerted_xiaojie_candidates(
                connection, trading_date, "潜龙出海_swing")),
            timeout_seconds=30,
        )
        known = {(str(item.get("symbol") or ""), str(item.get("mode") or "")) for item in fresh}
        fresh.extend(
            item for item in pending_qianlong
            if (str(item.get("symbol") or ""), str(item.get("mode") or "")) not in known
        )

        # Ordinary sealed boards remain excluded because there is no longer an
        # actionable entry/承接 observation.  潜龙出海 is the explicit exception:
        # the user asked to receive a research reminder even when it is sealed.
        # The alert text labels it as research-only and this strategy remains at
        # zero live weight; it never becomes an order candidate.
        actionable = [item for item in fresh if xiaojie_research_alert_allowed(item)]
        sealed_skipped = sum(
            1 for item in fresh
            if ((item.get("evidence") or {}).get("board") or {}).get("sealed")
            and not xiaojie_research_alert_allowed(item)
        )
        sealed_research_alerts = sum(
            1 for item in fresh
            if ((item.get("evidence") or {}).get("board") or {}).get("sealed")
            and xiaojie_research_alert_allowed(item)
        )
        # The budget is read from what the table already recorded, so a restart
        # mid-session cannot hand out a fresh allowance.
        sent = await self.deps.run_database(
            lambda: self.deps.with_connection(lambda connection: xiaojie_alerted_count(connection, trading_date)),
            timeout_seconds=30,
        )
        remaining = min(MAX_ALERTS_PER_SCAN, max(0, MAX_ALERTS_PER_SESSION - sent))
        # Alert slots are scarce, so they go to the highest-conviction setups
        # rather than to whichever mode happens to be most numerous.
        actionable = sorted(actionable, key=xiaojie_alert_priority)
        alerted: list[tuple[str, str]] = []
        alert_errors: list[str] = []
        for candidate in actionable[:remaining]:
            try:
                event_id = await self.deps.run_database(
                    lambda item=candidate: self.deps.with_connection(
                        lambda connection: persist_signal_event(connection, scan_id, observed_at, item)),
                    timeout_seconds=30,
                )
                await self.deps.deliver_alert(
                    event_id, alert_text(
                        candidate, trading_date, reference.get("names"),
                        teacher=self.deps.teacher_plan(trading_date, candidate["symbol"]),
                        chat=await self.deps.chat_context(candidate["symbol"], observed_at),
                    ))
                alerted.append((candidate["symbol"], str(candidate.get("mode") or "unclassified")))
            except Exception as error:  # noqa: BLE001 - an alert failure must not end the scan
                alert_errors.append(f"{candidate.get('symbol')}: {self.deps.safe_error(str(error), 160)}")
        if alerted:
            await self.deps.run_database(
                lambda: self.deps.with_connection(lambda connection: mark_xiaojie_alerted(
                    connection, trading_date, observed_at, alerted)),
                timeout_seconds=30,
            )
        # Shadow-mode launch radar rides the same cross-section: the launch band
        # (past +5%, not yet leader-pool territory) is watched for the three-way
        # coincidence of volume burst, standing sector anchor and price velocity.
        # Research-only - observations settle through the shared outcomes table,
        # and no alert is ever sent from here.
        launch_status: dict[str, Any] = {"status": "skipped"}
        try:
            launch = evaluate_launch_radar(
                all_a_rows, limits=reference["limits"], membership=reference["membership"],
                references=reference["references"], pool=leader_pool_symbols(all_a_rows, reference["limits"]),
                velocity_state=self.launch_velocity_state, observed_at=observed_at,
                elapsed_session_minutes=int(result["market_gate"].get("elapsed_session_minutes") or 0),
            )
            launch_fresh = await self.deps.run_database(
                lambda: self.deps.with_connection(lambda connection: record_launch_radar_observations(
                    connection, trading_date, observed_at, scan_id, launch["candidates"])),
                timeout_seconds=30,
            ) if launch["candidates"] else 0
            launch_status = {"status": "completed", "band_size": launch["band_size"],
                             "candidates": len(launch["candidates"]), "new": launch_fresh,
                             "truncated": launch["truncated"]}
        except Exception as error:  # noqa: BLE001 - the radar must never end the scan
            launch_status = {"status": "failed", "reason": self.deps.safe_error(str(error), 200)}
        return {
            "status": "completed", "model_version": XIAOJIE_LEADER_FLOW_MODEL_VERSION,
            "launch_radar": launch_status,
            "pool_size": result["pool_size"], "evaluated": result["evaluated"],
            "main_sector_count": result["main_sector_count"],
            "regime": result["regime"],
            "candidates": len(candidates), "new_candidates": new_candidate_count, "alerted": len(alerted),
            "sector_flow": result.get("sector_flow"),
            "actionable_candidates": len(actionable),
            "sealed_skipped": sealed_skipped,
            "sealed_research_alerts": sealed_research_alerts,
            "alerts_suppressed_by_cap": max(0, len(actionable) - len(alerted)),
            "alerted_modes": sorted({mode for _symbol, mode in alerted}),
            "alerts_sent_this_session": sent + len(alerted),
            "alert_errors": alert_errors or None,
            "reference_symbols": len(reference["limits"]),
            "live_effect": "none", "boundary": "research_only; no_automatic_order",
        }


__all__ = [
    "MAX_ALERTS_PER_SCAN", "MAX_ALERTS_PER_SESSION", "XiaojieLeaderFlowDependencies",
    "XiaojieLeaderFlowRuntime", "persist_signal_event",
]
