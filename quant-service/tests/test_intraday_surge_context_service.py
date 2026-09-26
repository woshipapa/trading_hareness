from __future__ import annotations

import asyncio
import unittest

from app.intraday_surge_context_service import capture


class IntradaySurgeContextServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_circuit_open_returns_explicit_watch_basket_without_fetching(self):
        called = False

        async def fetch(_symbol: str):
            nonlocal called
            called = True
            return []

        async def open_capabilities(_provider, _capabilities):
            return {"tencent_intraday_minute"}

        async def run_database(*_args, **_kwargs):
            raise AssertionError("circuit-open path must not persist a failed provider call")

        features, evidence = await capture(
            [{"symbol": "000001.SZ", "metadata": {"surge_strategy": {"enabled": True, "peer_symbols": ["000002.SZ"]}}}],
            mapped_peers={"000001.SZ": {"peer_symbols": ["000003.SZ"]}}, cache={}, max_symbols=lambda: 20,
            open_capabilities=open_capabilities, capability="tencent_intraday_minute", fetch_minutes=fetch,
            minute_features=lambda rows, **_kwargs: {"rows": rows}, persist_health=lambda *_args: None,
            run_database=run_database, safe_error=lambda value, _limit: value,
            handled_errors=(asyncio.TimeoutError, ValueError),
        )
        self.assertFalse(called)
        self.assertEqual(features, {})
        self.assertEqual(evidence["requested"], ["000001.SZ", "000002.SZ", "000003.SZ"])
        self.assertEqual(evidence["provider_status"], "circuit_open")

    async def test_cache_prevents_second_provider_call(self):
        calls: list[str] = []
        health: list[tuple[object, ...]] = []
        cache: dict[str, tuple[float, dict[str, object] | None, str | None]] = {}

        async def fetch(symbol: str):
            calls.append(symbol)
            return [{"time": "10:00", "close": 1}]

        async def open_capabilities(_provider, _capabilities):
            return set()

        async def run_database(action, *args, **_kwargs):
            return action(*args)

        kwargs = dict(
            mapped_peers=None, cache=cache, max_symbols=lambda: 20,
            open_capabilities=open_capabilities, capability="tencent_intraday_minute", fetch_minutes=fetch,
            minute_features=lambda rows, **_kwargs: {"latest": rows[-1]}, persist_health=lambda *args: health.append(args),
            run_database=run_database, safe_error=lambda value, _limit: value,
            handled_errors=(asyncio.TimeoutError, ValueError),
        )
        watches = [{"symbol": "000001.SZ", "metadata": {}}]
        first, first_evidence = await capture(watches, **kwargs)
        second, second_evidence = await capture(watches, **kwargs)
        self.assertEqual(calls, ["000001.SZ"])
        self.assertEqual(first, second)
        self.assertEqual(first_evidence["provider_status"], "completed")
        self.assertEqual(second_evidence["provider_status"], "cached")
        self.assertEqual(len(health), 1)
        self.assertEqual(health[0][0], 1)

    async def test_batch_fetch_uses_one_call_and_keeps_per_symbol_errors(self):
        single_calls: list[str] = []
        batch_calls: list[list[str]] = []
        health: list[tuple[object, ...]] = []

        async def fetch(symbol: str):
            single_calls.append(symbol)
            return []

        async def fetch_batch(symbols: list[str]):
            batch_calls.append(list(symbols))
            return {"000001.SZ": [{"time": "10:00", "close": 1}], "000002.SZ": "stale session"}

        async def open_capabilities(_provider, _capabilities):
            return set()

        async def run_database(action, *args, **_kwargs):
            return action(*args)

        features, evidence = await capture(
            [{"symbol": "000001.SZ", "metadata": {}}, {"symbol": "000002.SZ", "metadata": {}},
             {"symbol": "000003.SZ", "metadata": {}}],
            mapped_peers=None, cache={}, max_symbols=lambda: 20,
            open_capabilities=open_capabilities, capability="longhu_intraday_minute", fetch_minutes=fetch,
            minute_features=lambda rows, **_kwargs: {"latest": rows[-1]}, persist_health=lambda *args: health.append(args),
            run_database=run_database, safe_error=lambda value, _limit: value,
            handled_errors=(Exception,), fetch_minutes_batch=fetch_batch,
        )
        self.assertEqual(single_calls, [])
        self.assertEqual(batch_calls, [["000001.SZ", "000002.SZ", "000003.SZ"]])
        self.assertEqual(list(features), ["000001.SZ"])
        self.assertIn("000002.SZ", evidence["errors"])
        self.assertIn("000003.SZ", evidence["errors"])

    async def test_batch_deadline_misses_are_retried_first_on_the_next_scan(self):
        batch_calls: list[list[str]] = []

        async def fetch_batch(symbols: list[str]):
            batch_calls.append(list(symbols))
            return {symbol: ([{"time": "10:00", "close": 1}] if index == 0 else "minute_batch_deadline_exceeded")
                    for index, symbol in enumerate(symbols)}

        async def open_capabilities(_provider, _capabilities):
            return set()

        async def run_database(action, *args, **_kwargs):
            return action(*args)

        kwargs = dict(
            mapped_peers=None, cache={}, max_symbols=lambda: 20,
            open_capabilities=open_capabilities, capability="longhu_intraday_minute",
            fetch_minutes=lambda _symbol: None, minute_features=lambda rows, **_kwargs: {"latest": rows[-1]},
            persist_health=lambda *args: None, run_database=run_database, safe_error=lambda value, _limit: value,
            handled_errors=(Exception,), fetch_minutes_batch=fetch_batch,
        )
        watches = [{"symbol": symbol, "metadata": {}} for symbol in ("000001.SZ", "000002.SZ", "000003.SZ")]
        _, first = await capture(watches, **kwargs)
        await capture(watches, **kwargs)
        self.assertEqual(first["deadline_exceeded_symbols"], ["000002.SZ", "000003.SZ"])
        self.assertEqual(batch_calls[1], ["000002.SZ", "000003.SZ"])

    async def test_batch_failure_marks_every_symbol_without_raising(self):
        async def fetch_batch(_symbols: list[str]):
            raise RuntimeError("gateway down")

        async def open_capabilities(_provider, _capabilities):
            return set()

        async def run_database(action, *args, **_kwargs):
            return action(*args)

        features, evidence = await capture(
            [{"symbol": "000001.SZ", "metadata": {}}, {"symbol": "000002.SZ", "metadata": {}}],
            mapped_peers=None, cache={}, max_symbols=lambda: 20,
            open_capabilities=open_capabilities, capability="longhu_intraday_minute",
            fetch_minutes=lambda _symbol: None, minute_features=lambda rows, **_kwargs: {},
            persist_health=lambda *args: None, run_database=run_database, safe_error=lambda value, _limit: value,
            handled_errors=(Exception,), fetch_minutes_batch=fetch_batch,
        )
        self.assertEqual(features, {})
        self.assertEqual(sorted(evidence["errors"]), ["000001.SZ", "000002.SZ"])

    async def test_quote_anomaly_priority_precedes_capped_passive_basket(self):
        calls: list[str] = []

        async def fetch(symbol: str):
            calls.append(symbol)
            return [{"time": "10:00", "close": 1}]

        async def open_capabilities(_provider, _capabilities):
            return set()

        async def run_database(action, *args, **_kwargs):
            return action(*args)

        features, evidence = await capture(
            [{"symbol": "000001.SZ", "metadata": {}}, {"symbol": "300364.SZ", "metadata": {}}],
            priority_symbols=["300364.SZ"], mapped_peers=None, cache={}, max_symbols=lambda: 1,
            open_capabilities=open_capabilities, capability="tencent_intraday_minute", fetch_minutes=fetch,
            minute_features=lambda rows, **_kwargs: {"latest": rows[-1]}, persist_health=lambda *_args: None,
            run_database=run_database, safe_error=lambda value, _limit: value,
            handled_errors=(asyncio.TimeoutError, ValueError),
        )
        self.assertEqual(calls, ["300364.SZ"])
        self.assertEqual(list(features), ["300364.SZ"])
        self.assertEqual(evidence["priority"]["quote_anomaly_symbols"], ["300364.SZ"])


if __name__ == "__main__":
    unittest.main()
