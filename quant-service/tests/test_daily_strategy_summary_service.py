from __future__ import annotations

import unittest
from datetime import date

from app.daily_strategy_summary_service import LEARNING_WINDOW_LIMIT, build_daily_strategy_summary, terminal_for_exchange_date


class _Result:
    def __init__(self, row): self.row = row
    def fetchone(self): return self.row


class DailyStrategySummaryServiceTests(unittest.TestCase):
    def test_learning_window_is_bounded_and_marks_truncation(self):
        class Connection:
            def __init__(self): self.learning_params = None
            def execute(self, sql, params=()):
                if "FROM quant.intraday_signal_events" in sql and "s.state='alerted'" in sql:
                    self.learning_params = params
                    return _Rows([{"signal_event_id": index, "signal_type": "watch", "observed_at": None,
                                   "evidence": {}, "status": "matured", "raw_return": 0.01,
                                   "maximum_favorable_excursion": None, "maximum_adverse_excursion": None}
                                  for index in range(LEARNING_WINDOW_LIMIT + 1)])
                if "FROM quant.intraday_signal_events" in sql:
                    return _Rows([])
                if "FROM quant.intraday_signal_outcomes" in sql:
                    return _Rows([])
                if "FROM quant.post_close_strategy_runs" in sql or "FROM quant.strategy_review_runs" in sql:
                    return _Result(None)
                return _Rows([])

        class _Rows:
            def __init__(self, rows): self.rows = rows
            def fetchall(self): return self.rows

        connection = Connection()
        class Database:
            def transaction(self):
                class Context:
                    def __enter__(_self): return connection
                    def __exit__(_self, *_args): return False
                return Context()

        result = build_daily_strategy_summary(
            Database(), date(2026, 8, 21), readiness=lambda _connection: {"decision_ready": False},
            json_safe=lambda value: value, policy_review=lambda rows, **kwargs: {"rows": len(rows)},
        )
        self.assertEqual(connection.learning_params, (LEARNING_WINDOW_LIMIT + 1,))
        self.assertEqual(result["offline_policy_learning"]["source_window"]["rows"], LEARNING_WINDOW_LIMIT)
        self.assertTrue(result["offline_policy_learning"]["source_window"]["truncated"])

    def test_terminal_receipt_accepts_only_non_retry_delivery_states(self):
        class Connection:
            def __init__(self, row): self.row = row
            def execute(self, sql, params):
                self.sql, self.params = sql, params
                return _Result(self.row)

        complete = Connection({"?column?": 1})
        self.assertTrue(terminal_for_exchange_date(complete, date(2026, 8, 21)))
        self.assertIn("delivery_status=ANY", complete.sql)
        self.assertEqual(complete.params[1], ["sent", "disabled", "suppressed"])
        self.assertFalse(terminal_for_exchange_date(Connection(None), date(2026, 8, 21)))

    def test_suppressed_summary_with_blocked_post_close_is_retryable(self):
        class Connection:
            def execute(self, sql, params):
                self.sql, self.params = sql, params
                return _Result(None)

        connection = Connection()
        self.assertFalse(terminal_for_exchange_date(connection, date(2026, 8, 21)))
        self.assertIn("post_close", connection.sql)
