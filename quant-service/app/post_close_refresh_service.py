"""Application-level assembly for the bounded post-close refresh pipeline."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from .market_temperature_runtime import refresh as refresh_market_temperature
from .minute_cross_section_export import export_day as export_minute_panel
from .request_models import (
    AkShareProbeRequest,
    AnnouncementSyncRequest,
    FetchRunReconcileRequest,
    FullMarketDailySyncRequest,
    MarketSnapshotRequest,
    MarketUniverseSyncRequest,
    PostCloseStrategyRequest,
    SectorFlowSyncRequest,
    SnapshotRequest,
    StrategyDecisionRequest,
    StrategyPatternMiningRequest,
    WatchlistMainWaveResearchRequest,
)


POST_CLOSE_STAGE_ORDER = (
    "stale_fetch_runs", "trade_calendar", "analyst_text", "all_a_universe", "full_market_daily", "core_daily_controls", "daily_control_reconciliation", "index_context",
    "close_market_snapshot", "akshare_supplements", "ths_industry_flow", "ths_concept_flow_and_limit_strength",
    "market_flow_features", "limit_ladder", "limit_lift_pattern_mining", "cninfo_announcements",
    "board_review", "close_strategy_decision", "close_review", "longhu_supplemental_evidence", "analyst_outcomes", "analyst_intraday_outcomes",
    "analyst_scorecards", "analyst_expert_research", "post_close_strategy", "decision_research_closure",
    "watchlist_main_wave", "teacher_review_roll", "watch_daily_review", "xiaojie_outcomes", "research_snapshot",
    "market_temperature", "broad_etf_flow", "market_timing", "candidate_ledger", "minute_panel_export",
)

POST_CLOSE_TIMEOUT_OVERRIDES = {
    # The owner Longhu close is a bounded multi-page full-market fetch.  The
    # generic stage budget (90s) is shorter than the provider work and would
    # cancel the caller while the shielded worker keeps running.
    "all_a_universe": 900.0,
    # One Fuyao request plus a held-calendar read; never on a provider's critical path.
    "trade_calendar": 60.0,
    "full_market_daily": 900.0,
    "akshare_supplements": 240.0,
    "limit_lift_pattern_mining": 120.0,
    # Four bounded full-market control APIs run sequentially so an individual
    # provider's shared limiter remains authoritative.
    "core_daily_controls": 720.0,
    # A stale completed control receipt can require the same four API calls
    # plus a persisted coverage read-back.
    "daily_control_reconciliation": 720.0,
    # Outcome settlement scans retained evidence and is intentionally local.
    # Give both orchestration and the blocking repository the same bounded
    # window instead of inheriting the generic ten-second request budget.
    "analyst_outcomes": 300.0,
    "analyst_intraday_outcomes": 180.0,
    # Settles the session and re-freezes still-valid teacher plans from
    # bounded Longhu bar requests (two in flight at a time).
    "teacher_review_roll": 240.0,
    "watch_daily_review": 240.0,
    # Settles the session's 小杰 observations and refreshes the previous
    # session, whose next-open/next-close columns only exist from today.
    "xiaojie_outcomes": 120.0,
    # Normalizes every strategy's own persisted output for the session into one ledger.
    "candidate_ledger": 180.0,
    # A session's minute documents to one Parquet file (decision 0009); about 240 reads.
    "minute_panel_export": 600.0,
    "market_temperature": 180.0,
    "broad_etf_flow": 180.0,
    "market_timing": 120.0,
}

POST_CLOSE_STAGE_DEPENDENCIES = {
    # Daily controls are correctness prerequisites: downstream strategy stages
    # must not reason over missing adjustment, limit or suspension fields.
    # Independent source evidence may still finish and remains diagnosable.
    "index_context": ("daily_control_reconciliation",),
    "limit_ladder": ("daily_control_reconciliation",),
    "limit_lift_pattern_mining": ("daily_control_reconciliation", "limit_ladder"),
    "close_strategy_decision": ("daily_control_reconciliation",),
    "close_review": ("daily_control_reconciliation",),
    "post_close_strategy": ("daily_control_reconciliation",),
    "watchlist_main_wave": ("daily_control_reconciliation",),
    "research_snapshot": ("daily_control_reconciliation",),
    "longhu_supplemental_evidence": ("full_market_daily", "daily_control_reconciliation"),
    "decision_research_closure": ("post_close_strategy", "daily_control_reconciliation"),
    # Settlement and next-session plans read the day's canonical bars and
    # point-in-time adjustment factors.
    "teacher_review_roll": ("full_market_daily", "daily_control_reconciliation"),
    "watch_daily_review": ("full_market_daily", "daily_control_reconciliation"),
    "xiaojie_outcomes": ("full_market_daily", "daily_control_reconciliation"),
    # The ledger reads what the strategy stages persisted; before them it would
    # record a session with half its lines missing.
    "candidate_ledger": ("daily_control_reconciliation", "post_close_strategy", "limit_lift_pattern_mining"),
}


@dataclass(frozen=True)
class PostCloseRefreshDependencies:
    """Local actions required by one post-close refresh; no provider is owned here."""

    database: Any
    china_today: Callable[[], date]
    longhu_configured: Callable[[], bool]
    longhu_close_context: Callable[[date], dict[str, Any]]
    provider_configs: Callable[[], dict[str, Any]]
    run_database: Callable[..., Awaitable[Any]]
    reconcile_stale_fetch_runs: Callable[[Any], Any]
    reprocess_remote_reports: Callable[..., Any]
    sync_market_universe: Callable[[Any], Awaitable[dict[str, Any]]]
    sync_full_market_daily: Callable[[Any], Awaitable[dict[str, Any]]]
    sync_strategy_index_context: Callable[[date], Awaitable[dict[str, Any]]]
    build_market_snapshot: Callable[[Any], Awaitable[dict[str, Any]]]
    load_core_symbols: Callable[[int], Awaitable[list[str]]]
    akshare_probe: Callable[[Any], Awaitable[dict[str, Any]]]
    sync_ths_industry_flow: Callable[[Any], Awaitable[dict[str, Any]]]
    sync_ths_concept_flow: Callable[[Any], Awaitable[dict[str, Any]]]
    rebuild_market_flow_features: Callable[..., Any]
    # Reports the day's captured limit-pool evidence; nothing is requested.
    limit_evidence: Callable[[date], Awaitable[dict[str, Any]]]
    persist_settled_limit_pool: Callable[[Any, date], dict[str, Any]]
    run_pattern_mining: Callable[[Any], Awaitable[dict[str, Any]]]
    sync_daily_controls: Callable[[date], Awaitable[dict[str, Any]]]
    sync_cninfo_announcements: Callable[[Any], Awaitable[dict[str, Any]]]
    run_board_report: Callable[..., Awaitable[dict[str, Any]]]
    run_strategy_decision: Callable[[Any], Awaitable[dict[str, Any]]]
    persist_close_review: Callable[[date], Any]
    recompute_outcomes: Callable[[date], Any]
    recompute_intraday_outcomes: Callable[[date], Any]
    recompute_scorecards: Callable[[date], Any]
    rebuild_analyst_research: Callable[[date], Any]
    run_post_close_strategy: Callable[[Any], Any]
    refresh_decision_research: Callable[[Any, date], dict[str, Any]]
    persist_watchlist_main_wave: Callable[[Any], Any]
    build_research_snapshot: Callable[[Any], Any]
    run_orchestrator: Callable[..., Awaitable[dict[str, Any]]]
    record_stage: Callable[..., Awaitable[Any]]
    lease_key: str
    lease_seconds: Callable[[], int]
    acquire_lease: Callable[..., Any]
    renew_lease: Callable[..., Any]
    release_lease: Callable[..., Any]
    safe_error_detail: Callable[[str, int], str]
    json_safe: Callable[[Any], Any]
    longhu_supplemental_sync: Callable[[date], Awaitable[dict[str, Any]]] | None = None
    materialize_candidate_ledger: Callable[[date], Any] | None = None
    teacher_review_roll: Callable[[date], Awaitable[dict[str, Any]]] | None = None
    watch_daily_review: Callable[[date], Awaitable[dict[str, Any]]] | None = None
    xiaojie_outcomes: Callable[[date], Awaitable[dict[str, Any]]] | None = None
    reconcile_daily_controls: Callable[[date], Awaitable[dict[str, Any]]] | None = None
    # Forward exchange calendar (Tushare trade_cal's replacement since 2026-10-08).
    sync_forward_calendar: Callable[[], Awaitable[dict[str, Any]]] | None = None
    # Decision 0013: the broad-ETF basket bars and their daily turnover ratio.
    refresh_broad_etf_flow: Callable[[date], Awaitable[dict[str, Any]]] | None = None
    # Decision 0013: the golden / silver finger (2560) state of the SSE composite.
    refresh_market_timing: Callable[[date], Awaitable[dict[str, Any]]] | None = None


async def run_post_close_refresh(request: Any, dependencies: PostCloseRefreshDependencies) -> dict[str, Any]:
    """Assemble the same bounded post-close stages and durable receipts.

    The function creates no provider client and contains no historical replay
    path.  It only preserves the existing same-date refresh workflow.
    """
    trade_date = request.trade_date or dependencies.china_today()
    longhu_mode = dependencies.longhu_configured()
    super_get = dependencies.provider_configs().get("super_get")
    # Longhu owns the licensed close path.  Passing an explicit Tushare
    # provider here bypasses the Longhu branch in ``main`` and was the reason
    # a configured Longhu close silently started with an empty Super GET
    # response.  Keep the explicit route only for the non-Longhu fallback.
    full_market_daily_provider = (
        "auto"
        if longhu_mode
        else (
            "super_get"
            if super_get and super_get.configured and super_get.get_gateway_mode == "promax" and super_get.supports("daily")
            else "auto"
        )
    )
    core_symbols: list[str] = []

    async def akshare_stage() -> dict[str, Any]:
        nonlocal core_symbols
        core_symbols = await dependencies.load_core_symbols(request.announcement_limit)
        if longhu_mode:
            return {
                "status": "skipped",
                "reason": "optional AkShare probe is outside the Longhu authoritative close path",
                "core_symbols": len(core_symbols),
            }
        probe_symbol = core_symbols[0] if core_symbols else "000636.SZ"
        return await dependencies.akshare_probe(AkShareProbeRequest(
            symbol=probe_symbol, trade_date=trade_date,
            include_macro_cross_asset=request.include_macro_cross_asset, board_limit=30,
        ))

    async def announcements_stage() -> dict[str, Any]:
        if not request.include_announcements or not core_symbols:
            return {"status": "skipped", "reason": "disabled or core universe is empty"}
        return await dependencies.sync_cninfo_announcements(AnnouncementSyncRequest(
            symbols=core_symbols, universe_key="core", start_date=trade_date - timedelta(days=45),
            end_date=trade_date, max_pages_per_symbol=1,
        ))

    async def limit_ladder_stage() -> dict[str, Any]:
        if longhu_mode:
            return await dependencies.run_database(
                dependencies.persist_settled_limit_pool, dependencies.database, trade_date,
                timeout_seconds=60,
            )
        # The pools were captured during the session; without a close snapshot
        # the report is blocked, and so is the mining stage that depends on it.
        return await dependencies.limit_evidence(trade_date)

    actions: dict[str, Callable[[], Any]] = {
        "stale_fetch_runs": lambda: dependencies.run_database(
            dependencies.reconcile_stale_fetch_runs, FetchRunReconcileRequest(max_age_minutes=90),
        ),
        "trade_calendar": (
            dependencies.sync_forward_calendar
            if dependencies.sync_forward_calendar is not None
            else (lambda: {"status": "skipped", "reason": "forward calendar not wired"})
        ),
        "analyst_text": lambda: dependencies.run_database(dependencies.reprocess_remote_reports, dependencies.database, 500),
        "all_a_universe": lambda: dependencies.sync_market_universe(MarketUniverseSyncRequest()),
        "full_market_daily": lambda: dependencies.sync_full_market_daily(
            FullMarketDailySyncRequest(trade_date=trade_date, provider=full_market_daily_provider),
        ),
        "index_context": lambda: dependencies.sync_strategy_index_context(trade_date),
        "close_market_snapshot": lambda: dependencies.build_market_snapshot(
            # The close checkpoint is the owner-side collection boundary. Ask
            # the capability resolver to refresh Fuyao and its bounded public
            # fallbacks here; the snapshot gate still keeps those sources
            # supplemental unless a separately configured licensed feed exists.
            MarketSnapshotRequest(session="close", universe_key="all_a", refresh_public_quotes=True),
        ),
        "akshare_supplements": akshare_stage,
        "ths_industry_flow": (
            lambda: dependencies.run_database(dependencies.longhu_close_context, trade_date)
            if longhu_mode else
            dependencies.sync_ths_industry_flow(SectorFlowSyncRequest(trade_date=trade_date, provider="super"))
        ),
        "ths_concept_flow_and_limit_strength": (
            lambda: {
                "status": "skipped",
                "reason": "Longhu close supplies exact THS industry membership and flow, not concept flow",
                "provider": "longhuvip_composite",
            }
            if longhu_mode else
            dependencies.sync_ths_concept_flow(SectorFlowSyncRequest(trade_date=trade_date, provider="super"))
        ),
        "market_flow_features": lambda: dependencies.run_database(
            dependencies.rebuild_market_flow_features, dependencies.database, trade_date, trade_date, timeout_seconds=90,
        ),
        "limit_ladder": limit_ladder_stage,
        "limit_lift_pattern_mining": lambda: dependencies.run_pattern_mining(
            StrategyPatternMiningRequest(as_of_date=trade_date, refresh_limit_sources=False),
        ),
        "core_daily_controls": lambda: dependencies.sync_daily_controls(trade_date),
        "daily_control_reconciliation": lambda: (
            dependencies.reconcile_daily_controls(trade_date)
            if dependencies.reconcile_daily_controls is not None
            else dependencies.sync_daily_controls(trade_date)
        ),
        "cninfo_announcements": announcements_stage,
        "board_review": (
            lambda: dependencies.run_database(dependencies.longhu_close_context, trade_date)
            if longhu_mode else dependencies.run_board_report(deliver=False)
        ),
        "close_strategy_decision": (
            lambda: {
                "status": "skipped",
                "reason": "legacy provider-coupled close decision is superseded by the persisted post-close strategy stage",
                "replacement_stage": "post_close_strategy",
            }
            if longhu_mode else
            dependencies.run_strategy_decision(
                StrategyDecisionRequest(session="close", kind="all", limit=20, validate_tushare_realtime=False),
            )
        ),
        "close_review": lambda: dependencies.run_database(dependencies.persist_close_review, trade_date),
        "longhu_supplemental_evidence": (
            lambda: dependencies.longhu_supplemental_sync(trade_date)
            if dependencies.longhu_supplemental_sync is not None and longhu_mode
            else {"status": "skipped", "reason": "Longhu supplemental capture is disabled or not configured", "research_only": True}
        ),
        "teacher_review_roll": (
            (lambda: dependencies.teacher_review_roll(trade_date))
            if dependencies.teacher_review_roll is not None
            else (lambda: {"status": "skipped", "reason": "teacher review disabled", "research_only": True})
        ),
        "watch_daily_review": (
            (lambda: dependencies.watch_daily_review(trade_date))
            if dependencies.watch_daily_review is not None
            else (lambda: {"status": "skipped", "reason": "watch review not wired", "research_only": True})
        ),
        "xiaojie_outcomes": (
            (lambda: dependencies.xiaojie_outcomes(trade_date))
            if dependencies.xiaojie_outcomes is not None
            else (lambda: {"status": "skipped", "reason": "xiaojie settlement not wired", "research_only": True})
        ),
        "analyst_outcomes": lambda: dependencies.run_database(
            dependencies.recompute_outcomes, trade_date, timeout_seconds=300,
        ),
        "analyst_intraday_outcomes": lambda: dependencies.run_database(
            dependencies.recompute_intraday_outcomes, trade_date, timeout_seconds=180,
        ),
        "analyst_scorecards": lambda: dependencies.run_database(dependencies.recompute_scorecards, trade_date),
        "analyst_expert_research": lambda: dependencies.run_database(dependencies.rebuild_analyst_research, trade_date),
        "post_close_strategy": lambda: dependencies.run_database(
            dependencies.run_post_close_strategy, PostCloseStrategyRequest(as_of_date=trade_date),
        ),
        "decision_research_closure": lambda: dependencies.run_database(
            dependencies.refresh_decision_research, dependencies.database, trade_date, timeout_seconds=120,
        ),
        "watchlist_main_wave": lambda: dependencies.run_database(
            dependencies.persist_watchlist_main_wave, WatchlistMainWaveResearchRequest(as_of_date=trade_date),
        ),
        "research_snapshot": lambda: dependencies.run_database(
            dependencies.build_research_snapshot, SnapshotRequest(as_of_date=trade_date),
        ),
        # Until 2026-10-09 only the daily pipeline built the ledger, and nothing ran that
        # every session: the last ledger days were 09-18, 09-21, 09-22 and 09-29.
        "candidate_ledger": (
            (lambda: dependencies.run_database(dependencies.materialize_candidate_ledger, trade_date, timeout_seconds=180))
            if dependencies.materialize_candidate_ledger is not None
            else (lambda: {"status": "skipped", "reason": "candidate ledger not wired", "research_only": True})
        ),
        # A session stored only per symbol (before the switch) has no documents and reports "missing".
        "minute_panel_export": lambda: dependencies.run_database(
            export_minute_panel, dependencies.database, trade_date, timeout_seconds=600,
        ),
        # Decision 0013. It reads the bars and limits already stored for the session,
        # whichever pipeline wrote them, so it waits on no stage receipt.
        "market_temperature": lambda: dependencies.run_database(
            refresh_market_temperature, dependencies.database, trade_date, timeout_seconds=180,
        ),
        "broad_etf_flow": (
            (lambda: dependencies.refresh_broad_etf_flow(trade_date))
            if dependencies.refresh_broad_etf_flow is not None
            else (lambda: {"status": "skipped", "reason": "broad-ETF flow not wired", "research_only": True})
        ),
        "market_timing": (
            (lambda: dependencies.refresh_market_timing(trade_date))
            if dependencies.refresh_market_timing is not None
            else (lambda: {"status": "skipped", "reason": "market timing not wired", "research_only": True})
        ),
    }

    async def record_refresh_stage(name: str, stage_date: date, action: Callable[[], Any]) -> Any:
        return await dependencies.record_stage(
            name, stage_date, action, db=dependencies.database, run_database_blocking=dependencies.run_database,
            safe_error_detail=dependencies.safe_error_detail,
        )

    return await dependencies.run_orchestrator(
        request, db=dependencies.database, lease_key=dependencies.lease_key,
        lease_seconds=dependencies.lease_seconds, run_database_blocking=dependencies.run_database,
        acquire_lease=dependencies.acquire_lease, renew_lease=dependencies.renew_lease,
        release_lease=dependencies.release_lease, actions=actions, stage_order=POST_CLOSE_STAGE_ORDER,
        timeout_overrides=POST_CLOSE_TIMEOUT_OVERRIDES, stage_dependencies=POST_CLOSE_STAGE_DEPENDENCIES,
        record_stage=record_refresh_stage, trade_date=trade_date,
        safe_error_detail=dependencies.safe_error_detail, json_safe=dependencies.json_safe,
    )


__all__ = [
    "POST_CLOSE_STAGE_DEPENDENCIES", "POST_CLOSE_STAGE_ORDER", "POST_CLOSE_TIMEOUT_OVERRIDES",
    "PostCloseRefreshDependencies", "run_post_close_refresh",
]
