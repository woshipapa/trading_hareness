from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.tushare_normalization import normalize_rows

AVAILABLE_AT = datetime(2026, 9, 18, 7, 0, tzinfo=timezone.utc)
CORE = frozenset({"daily"})


def _row(ts_code: str, close: str = "10.0") -> dict:
    return {"ts_code": ts_code, "trade_date": "20260918", "open": close, "high": close,
            "low": close, "close": close, "pre_close": close, "vol": "100", "amount": "500"}


class _Connection:
    def __init__(self):
        self.issues: list[tuple] = []
        self.savepoints = 0
        self.rolled_back = 0

    def execute(self, statement, parameters=None):
        if "data_quality_issues" in statement:
            self.issues.append(tuple(parameters or ()))
        return SimpleNamespace(fetchone=lambda: None, fetchall=list)

    @contextmanager
    def transaction(self):
        self.savepoints += 1
        try:
            yield self
        except Exception:
            self.rolled_back += 1
            raise


class _DailyBasicBatchConnection(_Connection):
    def __init__(self):
        super().__init__()
        self.batches: list[tuple[str, list[tuple]]] = []

    @contextmanager
    def cursor(self):
        connection = self

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def executemany(self, statement, parameters):
                connection.batches.append((statement, list(parameters)))

        yield Cursor()


def _bar_type(**fields):
    return SimpleNamespace(**fields)


class DailyNormalizationBatchingTests(unittest.TestCase):
    """A cross-section is written in batches, but stays row-isolated where it matters."""

    def _run(self, rows, *, batch_fails=False, connection=None):
        seen = {"bars": [], "symbols": [], "per_row": []}

        def upsert_bars(_connection, bars):
            if batch_fails:
                raise RuntimeError("batched write rejected")
            seen["bars"].append(list(bars))
            return len(bars)

        def ensure_instruments(_connection, symbols):
            seen["symbols"].append(list(symbols))

        def upsert_bar(_connection, bar):
            seen["per_row"].append(bar)

        connection = connection or _Connection()
        normalized = normalize_rows(
            connection, "daily", rows, AVAILABLE_AT, core_apis=CORE,
            date_parser=lambda value: date(2026, 9, 18) if value else None,
            exchange_for=lambda symbol: symbol.rsplit(".", 1)[1],
            is_st_security_name=lambda _name: False,
            ensure_instrument=lambda *_args: seen.setdefault("ensure_one", []).append(_args[1]),
            upsert_bar=upsert_bar, daily_bar_type=_bar_type,
            decimal_or_none=lambda value: Decimal(str(value)) if value is not None else None,
            safe_error_detail=lambda message, _limit: message,
            provider_key="tushare_primary",
            upsert_bars=upsert_bars, ensure_instruments=ensure_instruments,
        )
        return normalized, seen, connection

    def test_a_whole_cross_section_is_written_in_one_batch(self):
        rows = [_row("600176.SH"), _row("000001.SZ"), _row("300750.SZ")]
        normalized, seen, connection = self._run(rows)
        self.assertEqual(normalized, 3)
        self.assertEqual(len(seen["bars"]), 1)
        self.assertEqual(len(seen["bars"][0]), 3)
        self.assertEqual(seen["symbols"], [["600176.SH", "000001.SZ", "300750.SZ"]])
        self.assertEqual(seen["per_row"], [])
        self.assertEqual(connection.issues, [])

    def test_a_malformed_row_is_still_warned_and_skipped_alone(self):
        rows = [_row("600176.SH"), {"ts_code": "nonsense", "trade_date": "20260918"}, _row("000001.SZ")]
        normalized, seen, connection = self._run(rows)
        self.assertEqual(normalized, 2)
        self.assertEqual([bar.symbol for bar in seen["bars"][0]], ["600176.SH", "000001.SZ"])
        self.assertEqual(len(connection.issues), 1)
        self.assertEqual(connection.issues[0][0], "daily")

    def test_a_rejected_batch_is_replayed_row_by_row_instead_of_lost(self):
        rows = [_row("600176.SH"), _row("000001.SZ")]
        normalized, seen, connection = self._run(rows, batch_fails=True)
        self.assertEqual(normalized, 2)
        self.assertEqual(seen["bars"], [])
        self.assertEqual([bar.symbol for bar in seen["per_row"]], ["600176.SH", "000001.SZ"])
        # The failed batch rolled back its savepoint rather than the caller's work.
        self.assertEqual(connection.savepoints, 1)
        self.assertEqual(connection.rolled_back, 1)

    def test_an_all_bad_response_never_opens_a_write_batch(self):
        normalized, seen, connection = self._run([{"ts_code": "bad", "trade_date": None}])
        self.assertEqual(normalized, 0)
        self.assertEqual(seen["bars"], [])
        self.assertEqual(connection.savepoints, 0)
        self.assertEqual(len(connection.issues), 1)

    def test_without_the_batch_callables_the_per_row_contract_is_unchanged(self):
        seen = []
        connection = _Connection()
        normalized = normalize_rows(
            connection, "daily", [_row("600176.SH"), _row("000001.SZ")], AVAILABLE_AT, core_apis=CORE,
            date_parser=lambda value: date(2026, 9, 18) if value else None,
            exchange_for=lambda symbol: symbol.rsplit(".", 1)[1],
            is_st_security_name=lambda _name: False,
            ensure_instrument=lambda *_args: None,
            upsert_bar=lambda _connection, bar: seen.append(bar), daily_bar_type=_bar_type,
            decimal_or_none=lambda value: Decimal(str(value)) if value is not None else None,
            safe_error_detail=lambda message, _limit: message, provider_key="tushare_primary",
        )
        self.assertEqual(normalized, 2)
        self.assertEqual(len(seen), 2)

    def test_daily_basic_uses_one_batched_projection_write(self):
        connection = _DailyBasicBatchConnection()
        rows = [
            {"ts_code": "600176.SH", "trade_date": "20260918", "close": "51.0"},
            {"ts_code": "000001.SZ", "trade_date": "20260918", "close": "12.0"},
        ]
        normalized = normalize_rows(
            connection, "daily_basic", rows, AVAILABLE_AT,
            core_apis=frozenset({"daily_basic"}),
            date_parser=lambda value: date(2026, 9, 18) if value else None,
            exchange_for=lambda symbol: symbol.rsplit(".", 1)[1],
            is_st_security_name=lambda _name: False,
            ensure_instrument=lambda *_args: None, upsert_bar=lambda *_args: None,
            daily_bar_type=_bar_type,
            decimal_or_none=lambda value: Decimal(str(value)) if value is not None else None,
            safe_error_detail=lambda message, _limit: message, provider_key="tushare_super_get",
        )
        self.assertEqual(normalized, 2)
        self.assertEqual(len(connection.batches), 1)
        self.assertEqual(len(connection.batches[0][1]), 2)
        self.assertIn("INSERT INTO quant.daily_fundamentals", connection.batches[0][0])


if __name__ == "__main__":
    unittest.main()
