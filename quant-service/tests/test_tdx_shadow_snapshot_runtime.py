import asyncio
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.datasources.contracts import CapabilityEvidence
from app.runtime_executors import ExecutorSaturatedError
from app.tdx_shadow_snapshot_runtime import (
    AGREEMENT_CAPABILITY,
    AGREEMENT_PROVIDER,
    SHADOW_CAPABILITY,
    SHADOW_PROVIDER,
    TdxShadowSnapshotDependencies,
    TdxShadowSnapshotRuntime,
    day_summary,
    persist_shadow_capture,
)


NOW = datetime(2026, 10, 12, 7, 31, 46, tzinfo=timezone.utc)
TDX_ROW = {
    "symbol": "000001.SZ", "code": "000001", "price": 11.73, "pre_close": 11.59,
    "open": 11.62, "high": 11.83, "low": 11.62, "pct_change": 1.2079,
    "volume_lots": 100, "amount": 117300.0,
}
FUYAO_ROW = {
    "symbol": "000001.SZ", "price": 11.73, "pct_change": 1.2079,
    "volume": 10000, "turnover": 117300.0,
}


def evidence():
    return CapabilityEvidence(rows=[TDX_ROW], warnings=("tdx_host=test",))


class TdxShadowCaptureTests(unittest.TestCase):
    def dependencies(self, *, fetch=None, session=True, recent=(NOW - timedelta(seconds=2), [FUYAO_ROW]),
                     acquire=None, run_database=None, persisted=None, storage=True):
        calls = []

        async def fetch_snapshot():
            return await (fetch or (lambda: asyncio.sleep(0, result=evidence())))()

        async def session_open(_now):
            if isinstance(session, BaseException):
                raise session
            return session

        async def storage_allowed():
            return storage, {"allow_nonessential_high_frequency": storage}

        async def acquire_slot(_provider, *, max_wait_seconds):
            if acquire:
                return await acquire(_provider, max_wait_seconds=max_wait_seconds)

        async def run_db(action, *args, **kwargs):
            calls.append((action, args, kwargs))
            if run_database:
                return await run_database(action, *args, **kwargs)
            return action(*args)

        def persist_capture(database, observed_at, rows, metadata, comparison):
            if persisted is not None:
                persisted.append((database, observed_at, rows, metadata, comparison))
            return {"document": {"rows": len(rows)}, "comparison": {"stored": 1}}

        deps = TdxShadowSnapshotDependencies(
            fetch_snapshot=fetch_snapshot, session_open=session_open, run_database=run_db,
            database=object(), recent_reference=lambda _now: recent,
            acquire_slot=acquire_slot, persist_capture=persist_capture, storage_allowed=storage_allowed,
        )
        return TdxShadowSnapshotRuntime(deps), calls

    def test_capture_stores_shadow_document_and_comparison(self):
        persisted = []
        runtime, calls = self.dependencies(persisted=persisted)

        result = asyncio.run(runtime.capture(NOW))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["provider"], SHADOW_PROVIDER)
        self.assertEqual(result["capability"], SHADOW_CAPABILITY)
        self.assertEqual(len(calls), 1)
        self.assertEqual(persisted[0][2][0]["price_source"], "tdx_public_all_a_snapshot")
        self.assertEqual(persisted[0][4]["status"], "compared")
        self.assertEqual(persisted[0][4]["provider_key"], AGREEMENT_PROVIDER)
        self.assertEqual(persisted[0][4]["capability"], AGREEMENT_CAPABILITY)

    def test_outside_session_does_not_read_or_write(self):
        runtime, calls = self.dependencies(session=False, fetch=lambda: (_ for _ in ()).throw(AssertionError()))
        result = asyncio.run(runtime.capture(NOW))
        self.assertEqual(result["status"], "outside_session")
        self.assertEqual(calls, [])

    def test_session_database_busy_does_not_read(self):
        runtime, calls = self.dependencies(
            session=ExecutorSaturatedError("tdx shadow calendar busy"),
            fetch=lambda: (_ for _ in ()).throw(AssertionError()),
        )
        result = asyncio.run(runtime.capture(NOW))
        self.assertEqual(result["status"], "database_busy")
        self.assertEqual(calls, [])

    def test_storage_pause_does_not_read(self):
        runtime, calls = self.dependencies(
            storage=False,
            fetch=lambda: (_ for _ in ()).throw(AssertionError()),
        )
        result = asyncio.run(runtime.capture(NOW))
        self.assertEqual(result["status"], "storage_paused")
        self.assertEqual(calls, [])

    def test_rate_limit_refusal_is_reported(self):
        async def refuse(_provider, *, max_wait_seconds):
            raise RuntimeError("rate_limited:tdx_public")

        runtime, _ = self.dependencies(acquire=refuse)
        with patch("app.tdx_shadow_snapshot_runtime.PublicProviderRateLimited", RuntimeError):
            result = asyncio.run(runtime.capture(NOW))
        self.assertEqual(result["status"], "rate_limited")

    def test_provider_error_is_reported_without_storage(self):
        async def fail():
            raise RuntimeError("tdx down")

        runtime, calls = self.dependencies(fetch=fail)
        result = asyncio.run(runtime.capture(NOW))
        self.assertEqual(result["status"], "failed")
        self.assertIn("tdx down", result["error"])
        self.assertEqual(calls, [])

    def test_database_saturation_is_reported(self):
        async def saturated(_action, *_args, **_kwargs):
            raise ExecutorSaturatedError("tdx_shadow blocking executor is saturated")

        runtime, _ = self.dependencies(run_database=saturated)
        result = asyncio.run(runtime.capture(NOW))
        self.assertEqual(result["status"], "database_busy")

    def test_no_reference_stores_a_no_reference_row(self):
        persisted = []
        runtime, _ = self.dependencies(recent=None, persisted=persisted)
        result = asyncio.run(runtime.capture(NOW))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(persisted[0][4]["status"], "no_reference")

    def test_busy_and_late_result_keep_one_read_task(self):
        started = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def fetch():
            calls.append("fetch")
            started.set()
            await release.wait()
            return evidence()

        runtime, _ = self.dependencies(fetch=fetch)

        async def scenario():
            with patch("app.tdx_shadow_snapshot_runtime.TDX_READ_TIMEOUT_SECONDS", 0.01):
                first = asyncio.create_task(runtime.capture(NOW))
                await started.wait()
                busy = await runtime.capture(NOW)
                timed_out = await first
            release.set()
            await asyncio.sleep(0)
            return busy, timed_out, calls

        busy, timed_out, calls = asyncio.run(scenario())
        self.assertEqual(busy["status"], "busy")
        self.assertEqual(timed_out["status"], "timeout")
        self.assertEqual(calls, ["fetch"])

    def test_cancelled_read_consumes_late_error_and_keeps_busy_slot(self):
        started = asyncio.Event()
        release = asyncio.Event()
        calls = []
        loop_errors = []

        async def fetch():
            calls.append("fetch")
            started.set()
            await release.wait()
            raise RuntimeError("late tdx failure")

        runtime, _ = self.dependencies(fetch=fetch)

        async def scenario():
            loop = asyncio.get_running_loop()
            loop.set_exception_handler(lambda _loop, context: loop_errors.append(context))
            capture = asyncio.create_task(runtime.capture(NOW))
            await started.wait()
            capture.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await capture
            busy = await runtime.capture(NOW)
            release.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            finished = await runtime.capture(NOW)
            return busy, finished

        busy, finished = asyncio.run(scenario())
        self.assertEqual(busy["status"], "busy")
        self.assertEqual(finished["status"], "failed")
        self.assertEqual(loop_errors, [])
        self.assertEqual(calls, ["fetch", "fetch"])

    def test_comparison_failure_propagates_after_document_write(self):
        with patch("app.tdx_shadow_snapshot_runtime.persist_document", return_value={"rows": 1}), \
             patch("app.tdx_shadow_snapshot_runtime.persist_timed_observations", side_effect=RuntimeError("comparison down")):
            with self.assertRaisesRegex(RuntimeError, "comparison down"):
                persist_shadow_capture(object(), NOW, [], {}, {"status": "compared"})


class TdxShadowSummaryTests(unittest.TestCase):
    def test_indicator_health_document_check_does_not_read_shadow_documents(self):
        from app.indicator_health import minute_document_checks

        calls = []

        class Connection:
            def execute(self, sql, params=()):
                calls.append((sql, params))
                return SimpleNamespace(fetchone=lambda: {"documents": 0})

        minute_document_checks(Connection(), date(2026, 10, 12), NOW)
        self.assertTrue(calls)
        self.assertTrue(all("a_share_minute_cross_section_shadow" not in sql for sql, _ in calls))
        self.assertTrue(all("fuyao_ths" in sql and "a_share_minute_cross_section" in sql for sql, _ in calls))

    def test_summary_reads_only_shadow_streams_and_aggregates_comparisons(self):
        docs = [{"effective_at": NOW, "normalized": {"rows": 2}},
                {"effective_at": NOW + timedelta(minutes=1), "normalized": {"rows": 2}},
                {"effective_at": NOW + timedelta(minutes=2), "normalized": {"rows": 2}}]
        comparisons = [{
            "effective_at": NOW, "normalized": {
                "status": "compared", "comparison": {
                    "price": {"within_one_tick": 1.0, "within_0_1_pct": 0.9, "within_0_5_pct": 1.0,
                              "largest_relative_gap": 0.02, "largest_relative_gap_symbol": "000001.SZ"},
                    "volume": {"median_ratio": 1.01, "within_1_pct": 0.8},
                    "turnover": {"median_ratio": 0.99, "within_1_pct": 0.7},
                    "seconds_apart": 2.0,
                },
            },
        }, {
            "effective_at": NOW + timedelta(minutes=1),
            "normalized": {"status": "no_reference"},
        }]

        class Connection:
            def execute(self, sql, params=()):
                if "a_share_minute_cross_section_shadow" in sql:
                    return SimpleNamespace(fetchall=lambda: docs)
                return SimpleNamespace(fetchall=lambda: comparisons)

        class Database:
            def transaction(self):
                class Tx:
                    def __enter__(self):
                        return Connection()

                    def __exit__(self, *_args):
                        return False
                return Tx()

        result = day_summary(Database(), date(2026, 10, 12))
        self.assertEqual(result["stored_minutes"], 3)
        self.assertEqual(result["documents_without_comparison"], 1)
        self.assertEqual(result["minutes_compared"], 1)
        self.assertEqual(result["no_reference_minutes"], 1)
        self.assertEqual(result["largest_price_gap"]["symbol"], "000001.SZ")
        self.assertEqual(result["worst_price_within_one_tick"], 1.0)
        self.assertEqual(result["median_volume_ratio"], 1.01)
        self.assertEqual(result["largest_seconds_apart"], 2.0)
