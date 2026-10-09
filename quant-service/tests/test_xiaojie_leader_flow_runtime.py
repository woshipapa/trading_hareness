"""The 小杰 leader-flow pass, moved out of main.py (docs/decisions/0008); it had no direct test."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from app import xiaojie_leader_flow_runtime as runtime_module
from app.xiaojie_leader_flow_runtime import XiaojieLeaderFlowDependencies, XiaojieLeaderFlowRuntime

OBSERVED = datetime(2026, 10, 9, 2, 0, tzinfo=timezone.utc)


def build(*, sent):
    delivered, marked = [], []

    async def run_database(action, *args, **_kwargs):
        return action(*args)

    async def deliver(event_id, text):
        delivered.append((event_id, text))

    async def nothing(*_args, **_kwargs):
        return None

    deps = XiaojieLeaderFlowDependencies(
        run_database=run_database, with_connection=lambda action: action(object()),
        fetch_limit_cross_section=nothing, refresh_confluence=nothing,
        teacher_plan=lambda _day, _symbol: None, chat_context=nothing, deliver_alert=deliver,
        safe_error=lambda value, limit: value[:limit],
    )
    flow = XiaojieLeaderFlowRuntime(deps)

    async def reference(_day):
        return {"limits": {"000001.SZ": 11.0}, "membership": {}, "references": {}, "names": {}}

    flow.session_context = reference

    async def board_flow(_day, _observed_at):
        return {"boards": {}}

    flow.board_flow_point = board_flow
    candidates = [{"symbol": f"00000{i}.SZ", "mode": "trend", "evidence": {"board": {}}} for i in range(1, 6)]
    patches = [
        patch.object(runtime_module, "evaluate_xiaojie_leader_pool", return_value={
            "candidates": candidates, "pool_size": 5, "evaluated": 5, "main_sector_count": 1, "regime": "normal",
            "market_gate": {"elapsed_session_minutes": 30}}),
        patch.object(runtime_module, "record_xiaojie_candidates", side_effect=lambda *_args: list(candidates)),
        patch.object(runtime_module, "read_unalerted_xiaojie_candidates", return_value=[]),
        patch.object(runtime_module, "xiaojie_alerted_count", return_value=sent),
        patch.object(runtime_module, "xiaojie_research_alert_allowed", return_value=True),
        patch.object(runtime_module, "persist_signal_event", side_effect=lambda *_args: uuid.uuid4()),
        patch.object(runtime_module, "mark_xiaojie_alerted", side_effect=lambda *args: marked.append(args[-1])),
        patch.object(runtime_module, "evaluate_launch_radar", return_value={
            "candidates": [], "band_size": 0, "truncated": False}),
        patch.object(runtime_module, "alert_text", side_effect=lambda candidate, *_args, **_kwargs: candidate["symbol"]),
    ]
    return flow, patches, delivered, marked


class SessionContextTests(unittest.TestCase):
    """The session load runs its real lambdas: a broken name in one fails here, not on the first scan."""

    def test_the_session_reference_loads_and_tencent_limits_are_stored_under_their_provider(self):
        from datetime import date

        persisted = []

        async def run_database(action, *args, **_kwargs):
            return action(*args)

        async def fetch_limits(_day):
            return ([{"ts_code": "600001.SH", "up_limit": 11.0}, {"ts_code": "000001.SZ", "up_limit": 12.0}],
                    "tencent_free")

        async def nothing(*_args, **_kwargs):
            return None

        flow = XiaojieLeaderFlowRuntime(XiaojieLeaderFlowDependencies(
            run_database=run_database, with_connection=lambda action: action(object()),
            fetch_limit_cross_section=fetch_limits, refresh_confluence=nothing,
            teacher_plan=lambda _day, _symbol: None, chat_context=nothing, deliver_alert=nothing,
            safe_error=lambda value, limit: value[:limit]))
        reads = iter([{}, {"600001.SH": 11.0, "000001.SZ": 12.0}])
        with patch.object(runtime_module, "read_xiaojie_trade_limits", side_effect=lambda *_args: next(reads)), \
                patch.object(runtime_module, "persist_xiaojie_trade_limit_rows",
                             side_effect=lambda _connection, _day, rows, provider, _at: persisted.append(provider) or len(rows)), \
                patch.object(runtime_module, "load_xiaojie_session_reference",
                             return_value={"limits": {"600001.SH": 11.0}, "membership": {}}):
            reference = asyncio.run(flow.session_context(date(2026, 10, 9)))
        self.assertEqual(reference["limits"], {"600001.SH": 11.0})
        self.assertEqual(persisted, ["tencent_free"])


class LeaderFlowRuntimeTests(unittest.TestCase):
    def test_without_a_cross_section_the_pass_is_skipped(self):
        flow, _patches, _delivered, _marked = build(sent=0)
        result = asyncio.run(flow.run(scan_id=uuid.uuid4(), observed_at=OBSERVED, all_a_rows=[]))
        self.assertEqual(result["status"], "skipped")

    def test_without_session_limits_the_pass_is_blocked(self):
        flow, _patches, _delivered, _marked = build(sent=0)

        async def no_limits(_day):
            return {"limits": {}}

        flow.session_context = no_limits
        result = asyncio.run(flow.run(scan_id=uuid.uuid4(), observed_at=OBSERVED, all_a_rows=[{"symbol": "x"}]))
        self.assertEqual(result, {"status": "blocked", "reason": "session trade limits unavailable"})

    def test_alerts_stop_at_the_session_cap_read_from_the_table(self):
        flow, patches, delivered, marked = build(sent=runtime_module.MAX_ALERTS_PER_SESSION - 2)
        for item in patches:
            item.start()
        try:
            result = asyncio.run(flow.run(scan_id=uuid.uuid4(), observed_at=OBSERVED, all_a_rows=[{"symbol": "x"}]))
        finally:
            for item in patches:
                item.stop()
        self.assertEqual(result["status"], "completed")
        self.assertEqual((result["alerted"], len(delivered)), (2, 2))
        self.assertEqual(result["alerts_suppressed_by_cap"], 3)
        self.assertEqual(result["alerts_sent_this_session"], runtime_module.MAX_ALERTS_PER_SESSION)
        self.assertEqual(len(marked[0]), 2)
        self.assertEqual(result["live_effect"], "none")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
