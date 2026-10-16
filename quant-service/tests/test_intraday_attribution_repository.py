from __future__ import annotations

from datetime import datetime, timezone
import unittest

from app.intraday_attribution_repository import refresh_signal_attributions


class _Result:
    def __init__(self, batches):
        self._batches = list(batches)

    def fetchmany(self, size):
        del size
        return self._batches.pop(0) if self._batches else []


class _Cursor:
    def __init__(self, owner):
        self.owner = owner

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def executemany(self, statement, params):
        self.owner.updates.append((statement, list(params)))


class _Connection:
    def __init__(self, batches):
        self.result = _Result(batches)
        self.selects = []
        self.updates = []

    def execute(self, statement, params):
        self.selects.append((statement, params))
        return self.result

    def cursor(self):
        return _Cursor(self)


class IntradayAttributionRepositoryTests(unittest.TestCase):
    def test_refresh_streams_rows_and_batches_only_changed_evidence(self):
        rows = [
            {"signal_event_id": "a", "signal_key": "a:watch", "signal_type": "watch",
             "conditions": {}, "evidence": {"attribution": {"stage": "same"}}},
            {"signal_event_id": "b", "signal_key": "b:watch", "signal_type": "watch",
             "conditions": {}, "evidence": {}},
            {"signal_event_id": "c", "signal_key": "c:watch", "signal_type": "watch",
             "conditions": {}, "evidence": {}},
        ]
        connection = _Connection([rows[:2], rows[2:]])

        def attribution(signal_key, *_args):
            return {"stage": "same"} if signal_key == "a:watch" else {"stage": "new"}

        changed = refresh_signal_attributions(
            connection,
            cutoff=datetime(2026, 8, 13, tzinfo=timezone.utc),
            attribution_fn=attribution,
            json_safe=lambda value: value,
            batch_size=2,
        )

        self.assertEqual(changed, 2)
        self.assertEqual(len(connection.selects), 1)
        self.assertEqual(len(connection.updates), 2)
        self.assertEqual([params[0][1] for _statement, params in connection.updates], ["b", "c"])
        self.assertTrue(all("UPDATE quant.intraday_signal_events" in statement for statement, _ in connection.updates))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
