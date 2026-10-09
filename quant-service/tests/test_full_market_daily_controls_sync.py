from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError

from app.full_market_daily_controls_sync import (
    CONTROL_PERSIST_TIMEOUT_SECONDS, SUSPENSION_PROVIDER, normalize_suspensions, sync, valid_rows,
)
from app.request_models import FullMarketDailyControlsSyncRequest


class FullMarketDailyControlsSyncTests(unittest.IsolatedAsyncioTestCase):
    def test_bar_factor_mirror_requires_complete_positive_cumulative_factor(self):
        source = Path("app/full_market_daily_controls_sync.py").read_text(encoding="utf-8")
        mirror_section = source[source.index("UPDATE quant.market_bars_daily"):source.index("UPDATE quant.market_bars_daily bar SET limit_up")]
        self.assertTrue(
            "raw->>'factor_semantics'" in mirror_section
            or "persisted_factor_semantics_sql" in source,
        )
        self.assertIn("factor.adj_factor>0", mirror_section)
        self.assertIn("persisted_factor_semantics_sql", source)
        self.assertIn("factor.available_at<", mirror_section)
        self.assertIn("array_position", mirror_section)
        self.assertIn("DISTINCT ON", mirror_section)
        self.assertNotIn("FROM LATERAL", mirror_section)

    def test_repair_contract_requires_one_explicit_trade_date(self):
        self.assertEqual(
            FullMarketDailyControlsSyncRequest(trade_date="2026-08-18").trade_date,
            date(2026, 8, 18),
        )
        with self.assertRaises(ValidationError):
            FullMarketDailyControlsSyncRequest()

    def test_valid_rows_is_exact_date_and_a_share_only(self):
        trade_date = date(2026, 8, 21)
        rows = valid_rows("adj_factor", [
            {"ts_code": "000001.SZ", "trade_date": "20260821", "adj_factor": 1},
            {"ts_code": "000001.SZ", "trade_date": "20260821", "adj_factor": 2},
            {"ts_code": "000300.SH", "trade_date": "20260820", "adj_factor": 3},
            {"ts_code": "000001.SH", "trade_date": "20260821", "adj_factor": 4},
            {"ts_code": "510300.SH", "trade_date": "20260821", "adj_factor": 5},
            {"ts_code": "900901.SH", "trade_date": "20260821", "adj_factor": 6},
            {"ts_code": "920819.BJ", "trade_date": "20260821", "adj_factor": 7},
            {"ts_code": "302132.SZ", "trade_date": "20260821", "adj_factor": 8},
        ], trade_date, lambda value: date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:8]}"))
        # The SSE composite index, an ETF and a B share do not count toward
        # the equity coverage gate.
        self.assertEqual(rows, [
            {"ts_code": "000001.SZ", "trade_date": "20260821", "adj_factor": 2},
            {"ts_code": "920819.BJ", "trade_date": "20260821", "adj_factor": 7},
            {"ts_code": "302132.SZ", "trade_date": "20260821", "adj_factor": 8},
        ])

    async def test_no_daily_cross_section_blocks_without_provider_calls(self):
        async def fetch(_day):
            raise AssertionError("provider must not be called")

        result = await sync(date(2026, 8, 21), expected_daily_rows=lambda _day: 0, fetch_suspensions=fetch,
                            **_common(_Recorder()))
        self.assertEqual(result["status"], "blocked")

    async def test_persisted_controls_and_fetched_suspensions_are_promoted_together(self):
        trade_date = date(2026, 10, 9)
        recorder = _Recorder()
        fetched_days: list[date] = []

        async def fetch(day):
            fetched_days.append(day)
            return [_suspended("000582.SZ", "2026-10-08 09:30:00"),
                    _suspended("603183.SH", "2026-09-30 09:30:00", end="2026-10-08 15:00:00")]

        result = await sync(trade_date, expected_daily_rows=lambda _day: 1, fetch_suspensions=fetch,
                            read_persisted_factor_controls=_factors, read_persisted_control_rows=_controls(),
                            **_common(recorder))
        self.assertEqual(result["status"], "completed", result.get("reason"))
        self.assertEqual(fetched_days, [trade_date])
        self.assertEqual(result["providers"], {
            "adj_factor": "owner_persisted_adjustment_factor", "daily_basic": "longhuvip_composite",
            "stk_limit": "longhuvip_composite", "suspend_d": SUSPENSION_PROVIDER,
        })
        self.assertEqual(result["normalized_rows"]["suspend_d"], 1, "603183.SH resumed before the open")
        self.assertEqual(result["st_evidence"]["status"], "retired_source")
        self.assertEqual(recorder.successes, [(SUSPENSION_PROVIDER, "suspension_all_a", 1)])
        statements = recorder.statements
        self.assertIn("UPDATE quant.canonical_bars_daily SET is_suspended=false,canonicalized_at=now() WHERE trading_date=%s",
                      statements)
        inserted = [params for sql, params in recorder.executed if "INSERT INTO quant.security_suspensions" in sql]
        self.assertEqual([params[0] for params in inserted], ["000582.SZ"])
        remarked = [sql for sql in statements if "SET is_suspended=true" in sql and "security_suspensions" in sql]
        self.assertEqual(len(remarked), 2, "both bar tables are re-marked from the selected provider")
        self.assertTrue(any("replay_readiness_daily_coverage" in sql for sql in statements))
        self.assertEqual(recorder.timeouts, [None, CONTROL_PERSIST_TIMEOUT_SECONDS])

    async def test_a_rerun_re_marks_persisted_suspensions_without_fetching(self):
        recorder = _Recorder()

        async def fetch(_day):
            raise AssertionError("persisted suspensions need no fetch")

        suspensions = {"rows": [{"ts_code": "000582.SZ", "trade_date": "20261009"}], "provider": SUSPENSION_PROVIDER}
        result = await sync(date(2026, 10, 9), expected_daily_rows=lambda _day: 1, fetch_suspensions=fetch,
                            read_persisted_factor_controls=_factors,
                            read_persisted_control_rows=_controls(suspend_d=suspensions),
                            **_common(recorder))
        self.assertEqual(result["status"], "completed", result.get("reason"))
        self.assertFalse([sql for sql, _ in recorder.executed if "INSERT INTO quant.security_suspensions" in sql])
        remarked = [(sql, params) for sql, params in recorder.executed
                    if "SET is_suspended=true" in sql and "security_suspensions" in sql]
        self.assertEqual([params[2] for _sql, params in remarked], [SUSPENSION_PROVIDER, SUSPENSION_PROVIDER])

    async def test_a_missing_owner_projection_blocks_and_names_the_retired_fallback(self):
        recorder = _Recorder()

        async def fetch(_day):
            raise AssertionError("no fetch when a persisted control is missing")

        result = await sync(date(2026, 10, 9), expected_daily_rows=lambda _day: 1, fetch_suspensions=fetch,
                            read_persisted_factor_controls=_factors,
                            read_persisted_control_rows=_controls(daily_basic=None),
                            **_common(recorder))
        self.assertEqual(result["status"], "blocked")
        self.assertIn("daily_basic: no complete owner projection", result["reason"])
        self.assertIn("Tushare fallback was retired", result["reason"])
        self.assertEqual(recorder.executed, [], "nothing is promoted")

    async def test_a_failed_suspension_fetch_blocks_and_is_recorded(self):
        recorder = _Recorder()

        async def fetch(_day):
            raise ValueError("Eastmoney RPT_CUSTOM_SUSPEND_DATA_INTERFACE returned 5 of 17 rows")

        result = await sync(date(2026, 10, 9), expected_daily_rows=lambda _day: 1, fetch_suspensions=fetch,
                            read_persisted_factor_controls=_factors, read_persisted_control_rows=_controls(),
                            **_common(recorder))
        self.assertEqual(result["status"], "blocked")
        self.assertIn("returned 5 of 17 rows", result["reason"])
        self.assertEqual([failure[:2] for failure in recorder.failures], [(SUSPENSION_PROVIDER, "suspension_all_a")])


class SuspensionNormalizationTests(unittest.TestCase):
    def test_the_session_s_suspended_a_shares_only(self):
        # Rows as RPT_CUSTOM_SUSPEND_DATA_INTERFACE returned them for 2026-10-09.
        rows = normalize_suspensions([
            _suspended("000582.SZ", "2026-10-08 09:30:00"),
            _suspended("002388.SZ", "2026-10-08 09:30:00", end="2026-10-21 15:00:00"),
            _suspended("603183.SH", "2026-09-30 09:30:00", end="2026-10-08 15:00:00"),
            _suspended("200016.SZ", "2026-09-04 09:30:00"),
            _suspended("600001.SH", "2026-10-09 10:30:00", end="2026-10-09 11:30:00"),
            _suspended("600002.SH", "2026-10-10 09:30:00"),
        ], date(2026, 10, 9))
        self.assertEqual([row["ts_code"] for row in rows], ["000582.SZ", "002388.SZ", "600001.SH"])
        self.assertEqual(rows[0]["trade_date"], "20261009")
        self.assertEqual(rows[0]["suspend_reason"], "刊登重要公告 / 连续停牌")


def _suspended(code, start, end=None):
    return {"SECUCODE": code, "SUSPEND_START_TIME": start, "SUSPEND_END_TIME": end,
            "SUSPEND_EXPIRE": "连续停牌", "SUSPEND_REASON": "刊登重要公告"}


async def _factors(_day, _expected):
    return {"rows": [{"ts_code": "000001.SZ", "trade_date": "20261009", "adj_factor": "1.2"}]}


def _controls(**overrides):
    rows = {
        "daily_basic": {"rows": [{"ts_code": "000001.SZ", "trade_date": "20261009"}], "provider": "longhuvip_composite"},
        "stk_limit": {"rows": [{"ts_code": "000001.SZ", "trade_date": "20261009", "limit_up": 11, "limit_down": 9}],
                      "provider": "longhuvip_composite"},
        "suspend_d": None,
    }
    rows.update(overrides)

    async def read(api_name, _day, _expected):
        return rows.get(api_name)

    return read


def _common(recorder):
    return {"run_database_blocking": recorder.run_db, "db": recorder.database,
            "safe_error_detail": lambda value, _limit: value, "executor_saturated_error": RuntimeError,
            "record_provider_success": lambda _c, provider, capability, rows, _ms: recorder.successes.append(
                (provider, capability, rows)),
            "record_provider_failure": lambda _c, provider, capability, error, _ms: recorder.failures.append(
                (provider, capability, error))}


class _Recorder:
    """A fake database that keeps every statement, plus the provider-health calls."""

    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []
        self.timeouts: list[int | None] = []
        self.successes: list[tuple] = []
        self.failures: list[tuple] = []
        recorder = self

        class Result:
            def fetchone(self):
                return {}

        class Connection:
            def execute(self, statement, params=()):
                recorder.executed.append((" ".join(statement.split()), params))
                return Result()

        class Database:
            def transaction(self):
                class Context:
                    def __enter__(self):
                        return Connection()

                    def __exit__(self, *_args):
                        return False
                return Context()

        self.database = Database()

    @property
    def statements(self):
        return [sql for sql, _params in self.executed]

    async def run_db(self, action, *args, **kwargs):
        self.timeouts.append(kwargs.get("timeout_seconds"))
        return action(*args)


if __name__ == "__main__":
    unittest.main()
