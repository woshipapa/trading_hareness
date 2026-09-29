from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import unittest

from app.ten_day_leader_rotation_intraday_research import evaluate_intraday_rotation_candidates
from app.ten_day_leader_rotation_intraday_service import (
    TenDayLeaderRotationIntradayDependencies,
    persist_ten_day_leader_rotation_intraday,
)


class _Database:
    @contextmanager
    def transaction(self):
        yield object()


def _dependencies(sentiment_cycle, persisted):
    def persist(_connection, *, run_id, scan_id, observations, json_safe):
        persisted.extend(observations)
        return len(observations)

    return TenDayLeaderRotationIntradayDependencies(
        database=_Database(), quote_from_all_a=lambda _row: None, quote_source=lambda _quote: "test",
        market_context_batch=lambda _connection, keys: {key: {"market_state": "broad_risk_on"} for key in keys},
        evaluate=evaluate_intraday_rotation_candidates, persist=persist, json_safe=lambda value: value,
        sentiment_cycle=sentiment_cycle,
    )


class TenDayLeaderRotationIntradayServiceTests(unittest.TestCase):
    observed_at = datetime(2026, 8, 24, 2, tzinfo=timezone.utc)
    candidate = {"symbol": "600001.SH", "board": "main", "board_rank": 1, "ten_day_return_pct": 12}

    def _run(self, sentiment_cycle):
        persisted: list[dict] = []
        seen: list[datetime] = []

        def reader(_connection, observed_at):
            seen.append(observed_at)
            return sentiment_cycle

        summary = persist_ten_day_leader_rotation_intraday(
            scan_id="scan", observed_at=self.observed_at,
            pool={"run": {"run_id": "run", "strategy_available_at": datetime(2026, 8, 21, 10, tzinfo=timezone.utc)}},
            candidates=[self.candidate], all_a_rows=[],
            quotes={"600001.SH": {"pct_change": 6.0, "price": 10}},
            minute_features={"600001.SH": {"above_vwap_pct": 0.2, "return_3m_pct": 0.8, "minute_volume_multiple": 1.7}},
            peer_contexts={"600001.SH": {"exact_membership_groups": [{"sector_key": "x"}],
                                            "available_peer_count": 2, "confirming_peer_count": 2,
                                            "confirming_breadth": 1.0}},
            dependencies=_dependencies(reader, persisted),
        )
        return summary, persisted, seen

    def test_the_prior_session_sentiment_reading_drives_the_cycle(self) -> None:
        summary, persisted, seen = self._run({"stage": "fermenting", "trading_date": "2026-08-21"})
        self.assertEqual(seen, [self.observed_at])
        self.assertEqual(summary["cycle_state"], "attack_accelerating")
        self.assertEqual(summary["shadow_eligible"], 1)
        self.assertFalse(persisted[0]["decision_eligible"])

    def test_no_reading_blocks_every_candidate(self) -> None:
        summary, persisted, _ = self._run(None)
        self.assertEqual(summary["cycle_state"], "unavailable")
        self.assertEqual(summary["shadow_eligible"], 0)
        self.assertEqual(persisted[0]["shadow_state"], "cycle_risk_blocked")


if __name__ == "__main__":
    unittest.main()
