"""Runtime-boundary coverage for retained intraday outcome settlement."""

from __future__ import annotations

from datetime import date, datetime, timezone
import unittest

from app.intraday_outcome_runtime import IntradayOutcomeRuntime, IntradayOutcomeRuntimeDependencies


class _Transaction:
    def __init__(self, connection):
        self._connection = connection

    def __enter__(self):
        return self._connection

    def __exit__(self, *_args):
        return None


class IntradayOutcomeRuntimeTests(unittest.TestCase):
    def test_recompute_uses_one_transaction_and_returns_attribution_receipt(self):
        connection = object()
        database = type("Database", (), {"transaction": lambda _self: _Transaction(connection)})()
        calls = []
        cutoff = datetime(2026, 8, 24, 7, 5, tzinfo=timezone.utc)

        def refresh(received_connection, *, cutoff):
            calls.append(("refresh", received_connection, cutoff))
            return 3

        def settle(received_connection, as_of_date, **kwargs):
            calls.append(("settle", received_connection, as_of_date, kwargs["cutoff"]))
            return {"status": "completed", "outcome_rows": 8}

        runtime = IntradayOutcomeRuntime(IntradayOutcomeRuntimeDependencies(
            database=database,
            outcome_cutoff=lambda _date: cutoff,
            refresh_attributions=refresh,
            settle=settle,
            horizons=(("5m", 5),),
            direction_for=lambda _signal_type: 1,
            metrics_for=lambda *_args: {},
            decimal_or_none=lambda value: value,
            barrier_spec_type=lambda: object(),
            triple_barrier_label=lambda *_args, **_kwargs: {},
            persist_barrier_outcome=lambda *_args, **_kwargs: None,
            return_decomposition=lambda *_args: {},
            json_safe=lambda value: value,
        ))

        result = runtime.recompute(date(2026, 8, 24))

        self.assertEqual(result, {"status": "completed", "outcome_rows": 8, "attribution_backfilled": 3})
        self.assertEqual(calls, [
            ("refresh", connection, cutoff),
            ("settle", connection, date(2026, 8, 24), cutoff),
        ])

    def test_recompute_commits_bounded_signal_batches_when_loader_is_configured(self):
        class Connection:
            def execute(self, *_args, **_kwargs):
                return None

        connections = [Connection(), Connection(), Connection()]

        class Database:
            def __init__(self):
                self.index = 0

            def transaction(self):
                transaction = _Transaction(connections[self.index])
                self.index += 1
                return transaction

        calls = []
        cutoff = datetime(2026, 8, 24, 7, 5, tzinfo=timezone.utc)

        def settle(connection, _as_of_date, **kwargs):
            ids = list(kwargs["signal_event_ids"])
            calls.append((connection, ids))
            return {"signals": len(ids), "outcome_rows": len(ids), "matured": 0, "pending": 0,
                    "skipped_without_entry_price": 0, "intraday_horizons": {"5m": len(ids)}}

        runtime = IntradayOutcomeRuntime(IntradayOutcomeRuntimeDependencies(
            database=Database(), outcome_cutoff=lambda _date: cutoff,
            refresh_attributions=lambda _connection, *, cutoff: 0,
            settle=settle, horizons=(("5m", 5),), direction_for=lambda _value: 1,
            metrics_for=lambda *_args: {}, decimal_or_none=lambda value: value,
            barrier_spec_type=lambda: object(), triple_barrier_label=lambda *_args, **_kwargs: {},
            persist_barrier_outcome=lambda *_args, **_kwargs: None,
            return_decomposition=lambda *_args: {}, json_safe=lambda value: value,
            load_signal_event_ids=lambda _connection, _cutoff: ["a", "b", "c"], signal_batch_size=2,
        ))

        result = runtime.recompute(date(2026, 8, 24))

        self.assertEqual([ids for _connection, ids in calls], [["a", "b"], ["c"]])
        self.assertEqual(result["signals"], 3)
        self.assertEqual(result["outcome_rows"], 3)
        self.assertEqual(result["intraday_horizons"], {"5m": 3})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
