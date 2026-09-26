from __future__ import annotations

import unittest
from unittest.mock import patch

from datetime import date

from app.factor_sql_lab import (
    MIN_FORMAL_HISTORY_CALENDAR_SPAN_DAYS, _bh_q_values, _formal_history_blockers, _formal_history_metrics,
    _materialize_evaluation_rows, _materialize_factor_scores,
    _point_in_time_industry_ready, _split_rows, evaluable_factor_keys, prepare_factor_panel, run_multi_factor_strategy_sql,
)
from app.owner_storage import TIERED_EVIDENCE_TABLES


class RecordingResult:
    def __init__(self, row=None):
        self.row = row or {"rows": 10, "symbols": 2, "days": 5}

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []


class RecordingConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if "SELECT count(*)::int AS count FROM factor_sql_strategy_trades" in sql:
            return RecordingResult({"count": 0})
        return RecordingResult()


class FactorSqlLabTests(unittest.TestCase):
    def test_evaluable_contract_contains_only_implemented_price_volume_factors(self):
        self.assertEqual(
            evaluable_factor_keys(),
            frozenset({
                "momentum_5d", "momentum_20d", "reversal_5d", "sma_gap_20d",
                "volatility_20d", "volume_ratio_20d", "intraday_strength",
            }),
        )

    def test_chronological_split_purges_both_boundaries(self):
        rows = [{"trading_date": index} for index in range(100)]
        splits, contract = _split_rows(rows, 5)
        self.assertEqual([len(splits[key]) for key in ("train", "validation", "test")], [55, 10, 15])
        self.assertEqual(contract["dropped_boundary_days"], 20)
        self.assertLess(splits["train"][-1]["trading_date"], splits["validation"][0]["trading_date"])
        self.assertLess(splits["validation"][-1]["trading_date"], splits["test"][0]["trading_date"])

    def test_benjamini_hochberg_is_monotone_and_keeps_missing(self):
        result = _bh_q_values({"a": 0.01, "b": 0.04, "c": 0.03, "d": None})
        self.assertAlmostEqual(result["a"] or 0, 0.03)
        self.assertAlmostEqual(result["b"] or 0, 0.04)
        self.assertAlmostEqual(result["c"] or 0, 0.04)
        self.assertIsNone(result["d"])

    def test_strategy_rejects_overlapping_periods_before_database_work(self):
        with self.assertRaisesRegex(ValueError, "avoid overlapping"):
            run_multi_factor_strategy_sql(
                None, "all_a", 1, 2,
                {"factors": ["momentum_20d"], "rebalance_days": 1, "hold_days": 5},
            )

    def test_sql_strategy_uses_shared_t_plus_one_exit_lag(self):
        connection = RecordingConnection()
        with patch("app.factor_sql_lab.prepare_factor_panel", return_value={}) as prepare_panel:
            result = run_multi_factor_strategy_sql(
                connection, "all_a", date(2026, 1, 1), date(2026, 3, 1),
                {"factors": ["momentum_20d"], "rebalance_days": 6, "hold_days": 5},
            )

        self.assertEqual(prepare_panel.call_args.args[-1], 6)
        self.assertEqual(result["parameters"]["effective_exit_lag"], 6)
        trade_insert_params = next(
            params for sql, params in connection.calls
            if "CREATE TEMP TABLE factor_sql_strategy_trades" in sql
        )
        self.assertEqual(trade_insert_params[2], 6)

    def test_strategy_applies_direction_overrides_and_research_mode(self):
        connection = RecordingConnection()
        with patch("app.factor_sql_lab.prepare_factor_panel", return_value={}) as prepare_panel:
            result = run_multi_factor_strategy_sql(
                connection, "all_a", date(2026, 1, 1), date(2026, 3, 1),
                {"factors": ["volume_ratio_20d", "reversal_5d"], "rebalance_days": 6, "hold_days": 5,
                 "directions": {"volume_ratio_20d": -1}, "membership_mode": "current_backfill"},
            )
        self.assertEqual(prepare_panel.call_args.kwargs["membership_mode"], "current_backfill")
        score_params = [params for sql, params in connection.calls if "INSERT INTO factor_sql_strategy_scores" in sql]
        self.assertEqual([params[1] for params in score_params], [-1.0, 1.0])     # override, then the registry prior
        self.assertEqual(result["metrics"]["assumptions"]["factor_directions"], {"volume_ratio_20d": -1.0, "reversal_5d": 1.0})
        self.assertIn("industry_membership_backfilled_from_current", result["metrics"]["promotion_gate"]["blockers"])
        with self.assertRaises(ValueError):
            run_multi_factor_strategy_sql(connection, "all_a", date(2026, 1, 1), date(2026, 3, 1),
                                          {"factors": ["reversal_5d"], "rebalance_days": 6, "hold_days": 5,
                                           "directions": {"reversal_5d": 2}})

    def test_strategy_reports_excess_over_an_equal_weight_benchmark(self):
        class Connection(RecordingConnection):
            def execute(self, sql, params=None):
                self.calls.append((sql, params))
                if "avg(trades.net_return) AS period_return" in sql:
                    class Rows(RecordingResult):
                        def fetchall(self_inner):
                            return [{"trading_date": date(2026, 1, 5), "period_return": 0.02, "positions": 20, "benchmark_return": 0.01},
                                    {"trading_date": date(2026, 1, 13), "period_return": -0.01, "positions": 20, "benchmark_return": -0.02}]
                    return Rows()
                return super().execute(sql, params)

        connection = Connection()
        with patch("app.factor_sql_lab.prepare_factor_panel", return_value={}):
            result = run_multi_factor_strategy_sql(connection, "all_a", date(2026, 1, 1), date(2026, 3, 1),
                                                   {"factors": ["reversal_5d"], "rebalance_days": 6, "hold_days": 5})
        benchmark = result["metrics"]["benchmark"]
        self.assertAlmostEqual(benchmark["mean_excess_per_period"], 0.01)
        self.assertEqual(benchmark["excess_win_rate"], 1.0)
        self.assertAlmostEqual(benchmark["total_return"], 1.01 * 0.98 - 1)
        benchmark_sql = next(sql for sql, _ in connection.calls if "CREATE TEMP TABLE factor_sql_strategy_benchmark" in sql)
        self.assertIn("entry.trading_index=signal.trading_index+1", benchmark_sql)
        self.assertEqual(result["equity_curve"][1]["benchmark_return"], -0.02)

    def test_formal_history_requires_calendar_span_as_well_as_trading_day_count(self):
        class Connection:
            def execute(self, _sql, _params):
                return RecordingResult({"days": 720, "first_date": date(2025, 1, 1), "last_date": date(2025, 12, 31)})

        history = _formal_history_metrics(Connection(), date(2025, 1, 1), date(2025, 12, 31))
        self.assertEqual(history["days"], 720)
        self.assertLess(history["calendar_span_days"], MIN_FORMAL_HISTORY_CALENDAR_SPAN_DAYS)
        self.assertIn("less_than_three_calendar_year_span", _formal_history_blockers(history))

    def test_panel_uses_point_in_time_membership_and_continuous_windows(self):
        connection = RecordingConnection()
        prepare_factor_panel(connection, "all_a", date(2026, 1, 1), date(2026, 3, 1), 5)
        create_sql = next(sql for sql, _ in connection.calls if "CREATE TEMP TABLE factor_sql_panel" in sql)
        self.assertIn("quant.universe_membership_history", create_sql)
        self.assertNotIn("quant.universe_members u", create_sql)
        self.assertIn("instrument.list_date IS NULL OR instrument.list_date<=bar.trading_date", create_sql)
        self.assertIn("instrument.delist_date IS NULL OR instrument.delist_date>=bar.trading_date", create_sql)
        self.assertIn("sector_membership_history", create_sql)
        self.assertIn("member.known_at", create_sql)
        self.assertIn("daily_adjustment_factors", create_sql)
        self.assertIn("adjustment.available_at", create_sql)
        self.assertNotIn("bar.adj_factor", create_sql)
        self.assertIn("bar.available_at < ((bar.trading_date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')", create_sql)
        self.assertIn("bar.quality_status='fresh'", create_sql)
        self.assertIn("daily_fundamentals basic", create_sql)
        self.assertIn("basic.available_at", create_sql)
        self.assertNotIn("LEFT JOIN quant.daily_fundamentals fundamental", create_sql)
        self.assertNotIn("instrument.industry", create_sql)
        self.assertIn("trading_index-index_20d_ago=20", create_sql)
        self.assertIn("bar.close*adjustment_history.adj_factor", create_sql)

    def test_backfill_mode_uses_current_industry_and_turnover_size_and_is_labelled(self):
        connection = RecordingConnection()
        panel = prepare_factor_panel(connection, "all_a", date(2023, 9, 1), date(2026, 9, 1), 5, "current_backfill")
        create_sql = next(sql for sql, _ in connection.calls if "CREATE TEMP TABLE factor_sql_panel" in sql)
        self.assertIn("'longhu_ths_industry','ths_index_i'", create_sql)
        self.assertIn("member.effective_to IS NULL", create_sql)
        self.assertNotIn("member.known_at <", create_sql)                    # not point-in-time, by design
        self.assertIn("'current_backfill' END AS industry_quality", create_sql)
        self.assertIn("ln(nullif(avg(amount)", create_sql)
        self.assertNotIn("bar.available_at <", create_sql)                   # backfilled history is kept
        self.assertNotIn("adjustment.available_at <", create_sql)
        self.assertIn("bar.quality_status IN ('fresh','partial')", create_sql)
        self.assertEqual(panel["membership_mode"], "current_backfill")
        connection.calls.clear()
        _materialize_factor_scores(connection, "momentum_20d", date(2023, 9, 1), date(2026, 9, 1), "current_backfill")
        score_sql = next(sql for sql, _ in connection.calls if "CREATE TEMP TABLE factor_sql_factor_scores" in sql)
        self.assertIn("signal.industry_quality='current_backfill'", score_sql)
        with self.assertRaises(ValueError):
            prepare_factor_panel(connection, "all_a", date(2023, 9, 1), date(2026, 9, 1), 5, "guess")

    def test_strict_mode_keeps_market_value_size(self):
        connection = RecordingConnection()
        prepare_factor_panel(connection, "all_a", date(2026, 1, 1), date(2026, 3, 1), 5)
        create_sql = next(sql for sql, _ in connection.calls if "CREATE TEMP TABLE factor_sql_panel" in sql)
        self.assertIn("log_market_cap_pit AS log_market_cap", create_sql)
        self.assertNotIn("longhu_ths_industry", create_sql)

    def test_panel_uses_atomic_owner_cold_relations_after_cutover(self):
        connection = RecordingConnection()
        connection.cursor = object()
        cold = {f"{name}_cold" for name in TIERED_EVIDENCE_TABLES}
        with patch("app.factor_sql_lab.eligible_cold_tables", return_value=cold):
            prepare_factor_panel(connection, "all_a", date(2025, 1, 1), date(2026, 3, 1), 5)
        create_sql = next(sql for sql, _ in connection.calls if "CREATE TEMP TABLE factor_sql_panel" in sql)
        for relation in ("canonical_bars_daily", "daily_adjustment_factors", "daily_fundamentals"):
            self.assertIn(f"quant.{relation}_cold", create_sql)
            self.assertIn(f"SELECT * FROM quant.{relation} UNION ALL", create_sql)

    def test_industry_gate_fails_closed_when_any_panel_row_is_unknown(self):
        self.assertTrue(_point_in_time_industry_ready({"rows": 10, "industry_pit_rows": 10}))
        self.assertFalse(_point_in_time_industry_ready({"rows": 10, "industry_pit_rows": 9}))
        self.assertFalse(_point_in_time_industry_ready({"rows": 0, "industry_pit_rows": 0}))

    def test_factor_score_candidates_exclude_unknown_industry_rows(self):
        connection = RecordingConnection()
        _materialize_factor_scores(connection, "momentum_20d", date(2026, 1, 1), date(2026, 3, 1))
        score_sql = next(sql for sql, _ in connection.calls if "CREATE TEMP TABLE factor_sql_factor_scores" in sql)
        self.assertIn("signal.industry_quality='point_in_time'", score_sql)

    def test_factor_standardization_does_not_select_on_future_outcome(self):
        connection = RecordingConnection()
        _materialize_factor_scores(connection, "momentum_20d", date(2026, 1, 1), date(2026, 3, 1))
        score_sql = next(sql for sql, _ in connection.calls if "CREATE TEMP TABLE factor_sql_factor_scores" in sql)
        self.assertNotIn("future", score_sql)
        connection.calls.clear()
        _materialize_evaluation_rows(connection, "momentum_20d", date(2026, 1, 1), date(2026, 3, 1), 5)
        evaluation_sql = next(sql for sql, _ in connection.calls if "CREATE TEMP TABLE factor_sql_evaluation" in sql)
        self.assertIn("JOIN factor_sql_panel future", evaluation_sql)


if __name__ == "__main__":
    unittest.main()
