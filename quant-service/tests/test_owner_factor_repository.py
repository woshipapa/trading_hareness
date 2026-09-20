from __future__ import annotations

import unittest
from datetime import date

from app.owner_factor_repository import read_persisted_factor_controls, read_persisted_factor_window


class OwnerFactorRepositoryTests(unittest.TestCase):
    def test_reads_only_complete_point_in_time_factor_rows(self):
        statements: list[str] = []

        class Result:
            def fetchall(self):
                return [{
                    "ts_code": "000001.SZ", "trade_date": "20260821",
                    "adj_factor": "1.2", "factor_provider": "longhu_qfq_derived",
                }]

        class Connection:
            def execute(self, statement, parameters):
                statements.append(" ".join(statement.split()))
                self.parameters = parameters
                return Result()

        payload = read_persisted_factor_controls(Connection(), date(2026, 8, 21))
        self.assertEqual(payload["providers"], ["longhu_qfq_derived"])
        self.assertEqual(payload["rows"][0]["ts_code"], "000001.SZ")
        self.assertIn("raw->>'factor_semantics'", statements[0])
        self.assertIn("corporate_action_cumulative", statements[0])
        self.assertIn("available_at<", statements[0])
        self.assertNotIn("provider=ANY(%s::text[])", statements[0])

    def test_symbol_filter_is_available_for_explicit_core_controls(self):
        seen: list[tuple[object, ...]] = []

        class Result:
            def fetchall(self): return []

        class Connection:
            def execute(self, _statement, parameters):
                seen.append(parameters)
                return Result()

        payload = read_persisted_factor_controls(Connection(), date(2026, 8, 21), ["000001.SZ"])
        self.assertEqual(payload["rows"], [])
        self.assertEqual(seen[0][-1], ["000001.SZ"])

    def test_on_demand_window_keeps_factor_availability_point_in_time(self):
        statements: list[str] = []

        class Result:
            def fetchall(self): return []

        class Connection:
            def execute(self, statement, _parameters):
                statements.append(" ".join(statement.split()))
                return Result()

        read_persisted_factor_window(Connection(), "000001.SZ", date(2026, 8, 1), date(2026, 8, 21))
        self.assertIn("available_at<((factor.trading_date+1)::timestamp AT TIME ZONE 'Asia/Shanghai')", statements[0])


if __name__ == "__main__":
    unittest.main()
