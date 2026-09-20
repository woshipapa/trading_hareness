from __future__ import annotations

import re
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from app.daily_bar_repository import persisted_adjustment_state, upsert_daily_bar, upsert_daily_bars
from app.request_models import DailyBar

AVAILABLE_AT = datetime(2026, 9, 18, 7, 0, tzinfo=timezone.utc)


def _bar(symbol: str, close: str, *, source: str = "tushare_primary", **overrides) -> DailyBar:
    fields = {
        "symbol": symbol, "trading_date": date(2026, 9, 18), "close": Decimal(close),
        "open": Decimal(close), "high": Decimal(close), "low": Decimal(close),
        "pre_close": Decimal(close), "volume": Decimal("1000"), "amount": Decimal("5000"),
        "source": source, "available_at": AVAILABLE_AT, "name": f"N{symbol[:3]}",
    }
    fields.update(overrides)
    return DailyBar(**fields)


def _squash(statement: str) -> str:
    return re.sub(r"\s+", " ", statement).strip()


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _Cursor:
    def __init__(self, connection):
        self._connection = connection
        self._returning: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def executemany(self, statement, parameters, returning=False):
        rows = list(parameters)
        for item in rows:
            self._connection.writes.append((_squash(statement), tuple(item)))
        if returning:
            self._returning = [self._connection.next_observation() for _ in rows]

    def fetchone(self):
        return self._returning[0] if self._returning else None

    def nextset(self):
        self._returning = self._returning[1:]
        return bool(self._returning)


class _Connection:
    """Records every write so two code paths can be compared statement by statement."""

    def __init__(self, existing=None):
        self.writes: list[tuple[str, tuple]] = []
        self.existing = dict(existing or {})
        self._observation = 0

    def next_observation(self):
        self._observation += 1
        return {"observation_id": f"obs-{self._observation}"}

    def cursor(self):
        return _Cursor(self)

    def execute(self, statement, parameters=None):
        squashed = _squash(statement)
        if squashed.startswith("SELECT"):
            if "unnest" in squashed:
                symbols, dates = parameters
                return _Result([self.existing[key] for key in zip(symbols, dates) if key in self.existing])
            key = (parameters[0], parameters[1])
            return _Result([self.existing[key]] if key in self.existing else [])
        self.writes.append((squashed, tuple(parameters or ())))
        if "RETURNING observation_id" in squashed:
            return _Result([self.next_observation()])
        return _Result([])


class DailyBarBatchEquivalenceTests(unittest.TestCase):
    """The batch exists only to spend fewer round trips, never to write differently.

    Each case runs the same bars through both paths against the same recorded
    connection and compares the writes as multisets: the batch groups by
    statement instead of by bar, so order across bars differs by construction,
    but no statement, parameter or evidence row may.
    """

    def _both(self, bars, existing=None):
        sequential = _Connection(existing)
        for bar in bars:
            upsert_daily_bar(sequential, bar)
        batched = _Connection(existing)
        upsert_daily_bars(batched, bars)
        # The owner-facing instrument statement is intentionally one sorted
        # ``unnest`` batch now; compare the bar/evidence semantics here rather
        # than requiring the old row-wise transport shape.
        def without_instrument(rows):
            return [row for row in rows if "quant.instruments" not in row[0]
                    and not row[0].startswith(("SAVEPOINT ", "SET LOCAL lock_timeout", "ROLLBACK TO SAVEPOINT", "RELEASE SAVEPOINT"))]
        return sorted(map(repr, without_instrument(sequential.writes))), sorted(map(repr, without_instrument(batched.writes)))

    def test_a_plain_cross_section_writes_exactly_the_same_rows(self):
        bars = [_bar("600176.SH", "51.0"), _bar("000001.SZ", "12.0"), _bar("300750.SZ", "180.5")]
        sequential, batched = self._both(bars)
        self.assertEqual(sequential, batched)

    def test_an_existing_canonical_row_is_arbitrated_identically(self):
        existing = {("600176.SH", date(2026, 9, 18)): {
            "symbol": "600176.SH", "trading_date": date(2026, 9, 18), "close": Decimal("50.0"),
            "selected_provider": "tushare_backup", "source_observation_ids": ["obs-prior"],
        }}
        # A better-priority provider replaces; the close disagreement is a warning.
        sequential, batched = self._both([_bar("600176.SH", "51.0")], existing)
        self.assertEqual(sequential, batched)
        self.assertTrue(any("provider_close_conflict" in write for write in batched))

    def test_a_worse_provider_keeps_the_existing_row_in_both_paths(self):
        existing = {("600176.SH", date(2026, 9, 18)): {
            "symbol": "600176.SH", "trading_date": date(2026, 9, 18), "close": Decimal("51.0"),
            "selected_provider": "tushare_primary", "source_observation_ids": ["obs-prior"],
        }}
        sequential, batched = self._both([_bar("600176.SH", "51.0", source="akshare")], existing)
        self.assertEqual(sequential, batched)
        self.assertTrue(any("UPDATE quant.canonical_bars_daily SET source_observation_ids" in w for w in batched))

    def test_a_quarantined_amount_produces_the_same_issue_and_partial_status(self):
        # amount/(volume*close) far outside the documented ratio band.
        bars = [_bar("600176.SH", "51.0", amount=Decimal("9000000"), volume=Decimal("1000"))]
        sequential, batched = self._both(bars)
        self.assertEqual(sequential, batched)
        self.assertTrue(any("daily_amount_unit_mismatch" in write for write in batched))
        self.assertTrue(any("'partial'" in write or "partial" in write for write in batched))

    def test_a_repeated_symbol_and_date_is_applied_in_arrival_order(self):
        bars = [_bar("600176.SH", "51.0"), _bar("600176.SH", "52.0", source="baostock")]
        sequential, batched = self._both(bars)
        self.assertEqual(sequential, batched)

    def test_front_adjusted_rows_are_still_refused(self):
        with self.assertRaises(ValueError):
            upsert_daily_bars(_Connection(), [_bar("600176.SH", "51.0", source="tencent_free")])

    def test_an_empty_batch_touches_nothing(self):
        connection = _Connection()
        self.assertEqual(upsert_daily_bars(connection, []), 0)
        self.assertEqual(connection.writes, [])

    def test_bar_adjustment_state_requires_a_licensed_source(self):
        self.assertEqual(
            persisted_adjustment_state(_bar("600176.SH", "51.0", adj_factor=Decimal("1.25"))),
            "complete",
        )
        self.assertEqual(
            persisted_adjustment_state(_bar("600176.SH", "51.0", source="longhuvip_composite", adj_factor=Decimal("1.25"))),
            "pending",
        )
        self.assertEqual(persisted_adjustment_state(_bar("600176.SH", "51.0")), "absent")


if __name__ == "__main__":
    unittest.main()
