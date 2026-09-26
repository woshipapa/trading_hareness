"""Regression coverage for continuous-session intraday settlement bounds."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import unittest
from unittest.mock import MagicMock

from app.intraday_attribution import signal_attribution
from app.intraday_clock import continuous_auction_bounds, intraday_outcome_window
from app.intraday_outcome_settlement import settle
from app.intraday_outcomes import a_share_return_decomposition, intraday_signal_outcome_metrics
from app.paper_execution import triple_barrier_label
from app.strategy_contracts import LabelSpec


class IntradaySettlementClockTests(unittest.TestCase):
    def test_morning_target_at_close_is_allowed_but_lunch_crossing_is_unavailable(self) -> None:
        # 11:25 Asia/Shanghai.
        entry = datetime(2026, 8, 11, 3, 25, tzinfo=timezone.utc)
        cutoff = datetime(2026, 8, 11, 3, 40, tzinfo=timezone.utc)

        at_close = intraday_outcome_window(entry, horizon_minutes=5, cutoff=cutoff)
        crosses_lunch = intraday_outcome_window(entry, horizon_minutes=15, cutoff=cutoff)

        self.assertEqual(at_close["status"], "unavailable")
        self.assertEqual(at_close["reason"], "exit_quote_missing_within_tolerance")
        self.assertEqual(at_close["target_at"], datetime(2026, 8, 11, 3, 30, tzinfo=timezone.utc))
        self.assertEqual(crosses_lunch["status"], "unavailable")
        self.assertEqual(crosses_lunch["reason"], "target_crosses_continuous_session_boundary")

    def test_target_before_cutoff_stays_pending_only_inside_its_tolerance(self) -> None:
        entry = datetime(2026, 8, 11, 1, 0, tzinfo=timezone.utc)  # 09:00? outside
        self.assertEqual(intraday_outcome_window(entry, horizon_minutes=5, cutoff=entry)["status"], "unavailable")

        entry = datetime(2026, 8, 11, 2, 0, tzinfo=timezone.utc)  # 10:00 Asia/Shanghai.
        before_target = intraday_outcome_window(entry, horizon_minutes=5, cutoff=datetime(2026, 8, 11, 2, 4, tzinfo=timezone.utc))
        awaiting_quote = intraday_outcome_window(entry, horizon_minutes=5, cutoff=datetime(2026, 8, 11, 2, 5, 30, tzinfo=timezone.utc))
        expired = intraday_outcome_window(entry, horizon_minutes=5, cutoff=datetime(2026, 8, 11, 2, 6, 31, tzinfo=timezone.utc))

        self.assertEqual(before_target["status"], "pending")
        self.assertEqual(before_target["reason"], "target_not_yet_observable")
        self.assertEqual(awaiting_quote["status"], "pending")
        self.assertEqual(awaiting_quote["reason"], "awaiting_exit_quote_within_tolerance")
        self.assertEqual(expired["status"], "unavailable")
        self.assertEqual(expired["reason"], "exit_quote_missing_within_tolerance")

    def test_continuous_bounds_do_not_treat_lunch_or_overnight_as_trade_time(self) -> None:
        self.assertIsNone(continuous_auction_bounds(datetime(2026, 8, 11, 4, 0, tzinfo=timezone.utc)))  # noon China.
        self.assertIsNone(continuous_auction_bounds(datetime(2026, 8, 11, 16, 0, tzinfo=timezone.utc)))
        afternoon = continuous_auction_bounds(datetime(2026, 8, 11, 5, 0, tzinfo=timezone.utc))
        self.assertIsNotNone(afternoon)
        self.assertEqual(afternoon[0], datetime(2026, 8, 11, 5, 0, tzinfo=timezone.utc))

    def test_non_eac_signal_with_shared_assessment_is_not_misattributed_as_eac(self) -> None:
        attribution = signal_attribution(
            "000001.SZ:watch:extreme_flow_buy", "watch",
            {"upside_research_assessment": {"status": "candidate"}}, {},
            number=lambda value: float(value) if value is not None else None,
            signal_model_version="watchlist-confirmation-v4",
        )
        self.assertEqual(attribution["stage"], "extension_watch")
        self.assertEqual(attribution["model_version"], "legacy-unversioned")
        self.assertEqual(attribution["volume_baseline"], "not_applicable")

    def test_trading_time_moves_a_lunch_crossing_target_into_the_afternoon(self) -> None:
        entry = datetime(2026, 8, 11, 3, 25, tzinfo=timezone.utc)  # 11:25 China.
        window = intraday_outcome_window(entry, horizon_minutes=15, cutoff=datetime(2026, 8, 11, 7, 0, tzinfo=timezone.utc),
                                         trading_time=True)
        self.assertEqual(window["target_at"], datetime(2026, 8, 11, 5, 10, tzinfo=timezone.utc))  # 13:10
        self.assertEqual(window["clock"], "trading_time_after_lunch")
        self.assertEqual(window["query_start"], datetime(2026, 8, 11, 5, 10, tzinfo=timezone.utc))
        late = intraday_outcome_window(datetime(2026, 8, 11, 6, 50, tzinfo=timezone.utc), horizon_minutes=15,
                                       cutoff=datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc), trading_time=True)
        self.assertEqual(late["reason"], "target_crosses_continuous_session_boundary")   # 14:50 + 15 is past the close

    @staticmethod
    def _settle(signal, *, quotes=(), tape=(), horizons=(("15m", 15),), cutoff=None):
        class Result:
            def __init__(self, *, rows=None, row=None):
                self.rows, self.row = rows or [], row

            def fetchall(self):
                return self.rows

            def fetchone(self):
                return self.row

        inserts: list[tuple[object, ...]] = []
        barrier = MagicMock()

        class Connection:
            def execute(self, query, params=None):
                text = str(query)
                if "FROM quant.intraday_signal_events" in text:
                    return Result(rows=[signal])
                if "watch_scan_tape" in text:
                    return Result(rows=[{"effective_at": at, "symbol": signal["symbol"], "price": price} for at, price in tape])
                if "FROM quant.intraday_quote_observations" in text:
                    assert "tencent_free" not in text                     # every priced source, not Tencent only
                    return Result(rows=[{"observed_at": at, "price": price} for at, price in quotes])
                if "FROM quant.canonical_bars_daily" in text:
                    return Result(row=None)
                if "INSERT INTO quant.intraday_signal_outcomes" in text:
                    inserts.append(tuple(params))
                return Result()

        result = settle(
            Connection(), date(2026, 8, 11), cutoff=cutoff or datetime(2026, 8, 11, 7, 0, tzinfo=timezone.utc),
            horizons=horizons, direction_for=lambda _signal_type: 1,
            metrics_for=intraday_signal_outcome_metrics,
            decimal_or_none=lambda value: Decimal(str(value)) if value is not None else None,
            barrier_spec_type=LabelSpec, triple_barrier_label=triple_barrier_label,
            persist_barrier_outcome=barrier, return_decomposition=a_share_return_decomposition,
            json_safe=lambda value: value,
        )
        return result, inserts, barrier

    def test_a_lunch_crossing_horizon_settles_in_the_afternoon_and_never_uses_a_lunch_print(self) -> None:
        signal = {"signal_event_id": "signal-1", "symbol": "000001.SZ", "signal_type": "entry",
                  "observed_at": datetime(2026, 8, 11, 3, 25, tzinfo=timezone.utc), "evidence": {"quote": {"price": "10.00"}}}
        exit_at = datetime(2026, 8, 11, 5, 10, 20, tzinfo=timezone.utc)          # 13:10:20
        quotes = [(datetime(2026, 8, 11, 4, 0, tzinfo=timezone.utc), "50.00"),    # a 12:00 print must never count
                  (exit_at, "10.30")]
        result, inserts, _ = self._settle(signal, quotes=quotes)
        row = next(params for params in inserts if params[1] == "15m")
        self.assertEqual(row[10], "matured")
        self.assertEqual(row[5], exit_at)
        self.assertEqual(row[7], Decimal("0.03"))
        self.assertEqual(row[8], Decimal("0.03"))                                 # MFE ignores the lunch print
        self.assertEqual(result["skipped_without_entry_price"], 0)

    def test_longhu_quotes_settle_and_a_signal_without_a_price_takes_its_entry_from_the_tape(self) -> None:
        signal_at = datetime(2026, 8, 11, 2, 0, 10, tzinfo=timezone.utc)          # 10:00:10
        signal = {"signal_event_id": "signal-2", "symbol": "000001.SZ", "signal_type": "watch",
                  "observed_at": signal_at, "evidence": {}}
        tape = [(datetime(2026, 8, 11, 2, 0, 5, tzinfo=timezone.utc), "10.00")]
        quotes = [(datetime(2026, 8, 11, 2, 5, 12, tzinfo=timezone.utc), "10.20")]  # a Longhu row
        result, inserts, _ = self._settle(signal, quotes=quotes, tape=tape, horizons=(("5m", 5),),
                                          cutoff=datetime(2026, 8, 11, 2, 10, tzinfo=timezone.utc))
        row = next(params for params in inserts if params[1] == "5m")
        self.assertEqual(row[4], Decimal("10.00"))                                # entry from the tape
        self.assertEqual(row[10], "matured")
        self.assertEqual(row[7], Decimal("0.02"))
        self.assertEqual(result["matured"], 1)

    def test_entry_price_prefers_the_scan_quote_then_the_strategy_price(self) -> None:
        from app.intraday_outcome_settlement import signal_entry_price
        number = lambda value: Decimal(str(value)) if value is not None else None  # noqa: E731
        self.assertEqual(signal_entry_price({"tencent": {"price": 9.58}, "price": 1}, number), (Decimal("9.58"), "signal_evidence.tencent.price"))
        self.assertEqual(signal_entry_price({"quote": {"price": 9.6}}, number), (Decimal("9.6"), "signal_evidence.quote.price"))
        self.assertEqual(signal_entry_price({"price": 3.51}, number), (Decimal("3.51"), "signal_evidence.price"))
        self.assertEqual(signal_entry_price({}, number), (None, None))

if __name__ == "__main__":
    unittest.main()
