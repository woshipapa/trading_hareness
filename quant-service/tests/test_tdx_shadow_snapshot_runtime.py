import asyncio
import threading
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.datasources.contracts import CapabilityEvidence
from app.runtime_executors import ExecutorSaturatedError, run_tdx_shadow_blocking
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
    return CapabilityEvidence(rows=[TDX_ROW] * 5301, warnings=("tdx_host=test",))


class TdxShadowCaptureTests(unittest.TestCase):
    def dependencies(self, *, fetch=None, session=True, recent=(NOW - timedelta(seconds=2), [FUYAO_ROW]),
                     acquire=None, run_database=None, persisted=None, storage=True, roster_total=None):
        calls = []

        async def fetch_snapshot():
            return await (fetch or (lambda: asyncio.sleep(0, result=evidence())))()

        def session_gate(_database, _now):
            return session, "test session"

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
            fetch_snapshot=fetch_snapshot, session_gate=session_gate, run_database=run_db,
            database=object(), recent_reference=lambda _now: recent,
            acquire_slot=acquire_slot, persist_capture=persist_capture, storage_allowed=storage_allowed,
            roster_total=roster_total or (lambda: asyncio.sleep(0, result=5580)),
        )
        return TdxShadowSnapshotRuntime(deps), calls

    def test_capture_stores_shadow_document_and_comparison(self):
        persisted = []
        runtime, calls = self.dependencies(persisted=persisted)

        result = asyncio.run(runtime.capture(NOW))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["provider"], SHADOW_PROVIDER)
        self.assertEqual(result["capability"], SHADOW_CAPABILITY)
        self.assertEqual([call[0] for call in calls], [runtime.deps.session_gate, runtime.deps.persist_capture])
        self.assertEqual(persisted[0][2][0]["price_source"], "tdx_public_all_a_snapshot")
        self.assertEqual(persisted[0][4]["status"], "compared")
        self.assertEqual(persisted[0][4]["provider_key"], AGREEMENT_PROVIDER)
        self.assertEqual(persisted[0][4]["capability"], AGREEMENT_CAPABILITY)
        self.assertTrue(persisted[0][3]["completeness"]["complete"])
        self.assertTrue(persisted[0][4]["completeness"]["complete"])

    def test_empty_snapshot_is_stored_as_incomplete(self):
        persisted = []

        async def empty():
            return CapabilityEvidence(rows=[], warnings=("no_trade_rows=5579",))

        runtime, _ = self.dependencies(fetch=empty, persisted=persisted)
        result = asyncio.run(runtime.capture(NOW))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(persisted[0][2], [])
        self.assertFalse(persisted[0][3]["completeness"]["complete"])
        self.assertEqual(persisted[0][4]["status"], "incomplete")
        self.assertIsNone(persisted[0][4]["comparison"])

    def test_roster_is_read_once_per_trade_date_and_none_is_not_remembered(self):
        totals = iter((None, 5580))
        calls = []

        async def roster_total():
            calls.append("read")
            return next(totals)

        runtime, _ = self.dependencies(roster_total=roster_total)
        first = asyncio.run(runtime.capture(NOW))
        second = asyncio.run(runtime.capture(NOW + timedelta(minutes=1)))
        third = asyncio.run(runtime.capture(NOW + timedelta(minutes=2)))

        self.assertEqual([first["status"], second["status"], third["status"]], ["completed"] * 3)
        self.assertEqual(calls, ["read", "read"])

    def test_outside_session_does_not_read_or_write(self):
        runtime, calls = self.dependencies(session=False, fetch=lambda: (_ for _ in ()).throw(AssertionError()))
        result = asyncio.run(runtime.capture(NOW))
        self.assertEqual(result["status"], "outside_session")
        self.assertEqual([call[0] for call in calls], [runtime.deps.session_gate])

    def test_session_gate_refused_or_timed_out_does_not_read(self):
        for error, text in ((ExecutorSaturatedError("tdx_shadow blocking executor is saturated"), "saturated"),
                            (TimeoutError(), "TimeoutError")):
            async def unavailable(_action, *_args, error=error, **_kwargs):
                raise error

            runtime, _ = self.dependencies(
                run_database=unavailable, fetch=lambda: (_ for _ in ()).throw(AssertionError()),
            )
            result = asyncio.run(runtime.capture(NOW))
            self.assertEqual(result["status"], "database_busy")
            self.assertIn(text, result["error"])

    def test_storage_pause_does_not_read(self):
        runtime, calls = self.dependencies(
            storage=False,
            fetch=lambda: (_ for _ in ()).throw(AssertionError()),
        )
        result = asyncio.run(runtime.capture(NOW))
        self.assertEqual(result["status"], "storage_paused")
        self.assertEqual([call[0] for call in calls], [runtime.deps.session_gate])

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
        self.assertEqual([call[0] for call in calls], [runtime.deps.session_gate])

    def test_persistence_refused_or_timed_out_is_reported(self):
        for error in (ExecutorSaturatedError("tdx_shadow blocking executor is saturated"), TimeoutError()):
            async def persistence_unavailable(action, *args, error=error, **_kwargs):
                if action is runtime.deps.session_gate:
                    return action(*args)
                raise error

            runtime, calls = self.dependencies(run_database=persistence_unavailable)
            result = asyncio.run(runtime.capture(NOW))
            self.assertEqual(result["status"], "database_busy")
            self.assertEqual([call[0] for call in calls], [runtime.deps.session_gate, runtime.deps.persist_capture])

    def test_the_service_gives_the_shadow_loop_only_its_own_database_runner(self):
        from app import main

        self.assertIs(main.tdx_shadow_runtime.deps.run_database, run_tdx_shadow_blocking)
        self.assertIs(main.tdx_shadow_runtime.deps.storage_allowed, main.tdx_shadow_storage_allowed)
        self.assertIs(main._tdx_shadow_storage_admission.run_database, run_tdx_shadow_blocking)

    def test_real_session_gate_and_persistence_run_on_the_shadow_worker(self):
        threads = []

        class OpenCalendar:
            @contextmanager
            def transaction(self):
                threads.append(threading.current_thread().name)
                yield SimpleNamespace(execute=lambda _sql, _params: SimpleNamespace(fetchone=lambda: {"is_open": True}))

        def persist_capture(_database, _observed_at, rows, _metadata, _comparison):
            threads.append(threading.current_thread().name)
            return {"document": {"rows": len(rows)}, "comparison": {"stored": 1}}

        async def allowed():
            return True, {}

        async def no_wait(_provider, *, max_wait_seconds):
            return None

        runtime = TdxShadowSnapshotRuntime(TdxShadowSnapshotDependencies(
            fetch_snapshot=lambda: asyncio.sleep(0, result=evidence()), run_database=run_tdx_shadow_blocking,
            database=OpenCalendar(), recent_reference=lambda _now: None, storage_allowed=allowed,
            acquire_slot=no_wait, persist_capture=persist_capture,
            roster_total=lambda: asyncio.sleep(0, result=5580),
        ))
        in_session = datetime(2026, 10, 12, 2, 0, tzinfo=timezone.utc)  # Monday 10:00 in Shanghai
        result = asyncio.run(runtime.capture(in_session))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(threads), 2)
        self.assertTrue(all(name.startswith("tdx-shadow") for name in threads))

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

    def test_main_roster_query_reads_payload_without_normalized(self):
        from app.minute_cross_section import latest_main_total

        calls = []

        class Connection:
            def execute(self, sql, params):
                calls.append((sql, params))
                return SimpleNamespace(fetchone=lambda: {"total": 5580})

        self.assertEqual(latest_main_total(Connection()), 5580)
        self.assertNotIn("normalized", calls[0][0])
        self.assertIn("payload", calls[0][0])


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
