import asyncio
import unittest
from datetime import date

from app.xiaojie_reference_repository import ensure_session_trade_limits

TRADING_DATE = date(2026, 9, 18)


class EnsureSessionTradeLimitsTests(unittest.TestCase):
    """The session cannot be scanned for board state without these prices."""

    def _run(self, *, existing=None, rows=None, stored=None, api=None):
        seen = {}

        async def read_limits(day):
            seen.setdefault("read_days", []).append(day)
            return dict(existing or {}) if len(seen["read_days"]) == 1 else {
                str(row["ts_code"]): float(row["up_limit"]) for row in (rows or [])
            }

        async def fetch_limit_cross_section(day):
            seen["fetched_day"] = day
            if api is not None:
                return await api(day)
            return list(rows or []), "tencent_free"

        async def persist_limits(day, persisted):
            seen["persisted"] = (day, persisted)
            return stored if stored is not None else len(persisted)

        result = asyncio.run(ensure_session_trade_limits(
            TRADING_DATE, read_limits=read_limits,
            fetch_limit_cross_section=fetch_limit_cross_section,
            persist_limits=persist_limits,
        ))
        return result, seen

    def test_an_already_provisioned_session_makes_no_provider_call(self):
        result, seen = self._run(existing={"600176.SH": 51.0})
        self.assertEqual(result["status"], "already_present")
        self.assertNotIn("fetched_day", seen)

    def test_a_full_market_cross_section_is_persisted_and_read_back(self):
        rows = ([{"ts_code": f"{600000 + index}.SH", "up_limit": "10.0"} for index in range(2320)]
                + [{"ts_code": f"{index:06d}.SZ", "up_limit": "10.0"} for index in range(2939)])
        result, seen = self._run(rows=rows)
        self.assertEqual(result["status"], "fetched")
        self.assertEqual(result["symbols"], 5259)
        self.assertEqual(result["provider"], "tencent_free")
        self.assertEqual(seen["persisted"][0], TRADING_DATE)

    def test_rows_without_a_symbol_are_not_persisted(self):
        rows = [{"ts_code": "600176.SH", "up_limit": "51.0"},
                {"ts_code": "000001.SZ", "up_limit": "12.0"}, {"ts_code": "  ", "up_limit": "1.0"}]
        _result, seen = self._run(rows=rows)
        self.assertEqual(len(seen["persisted"][1]), 2)

    def test_a_single_exchange_cross_section_is_rejected_unpersisted(self):
        # 2026-09-18: the first page returned 1,999 Shanghai rows against a
        # 2,000-row request, the loop read that as the terminal page, and the
        # session ran on a limit table with no Shenzhen names at all.
        rows = [{"ts_code": f"{600000 + index}.SH", "up_limit": "10.0"} for index in range(1999)]
        result, seen = self._run(rows=rows)
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("SZ", result["reason"])
        self.assertNotIn("persisted", seen)

    def test_a_cross_section_spanning_both_exchanges_is_accepted(self):
        rows = ([{"ts_code": f"{600000 + index}.SH", "up_limit": "10.0"} for index in range(1200)]
                + [{"ts_code": f"{index:06d}.SZ", "up_limit": "10.0"} for index in range(1200)])
        result, _seen = self._run(rows=rows)
        self.assertEqual(result["status"], "fetched")

    def test_an_empty_response_reports_unavailable_rather_than_persisting(self):
        result, seen = self._run(rows=[])
        self.assertEqual(result["status"], "unavailable")
        self.assertNotIn("persisted", seen)

    def test_a_provider_failure_propagates_instead_of_leaving_a_partial_table(self):
        async def failing(*_args, **_kwargs):
            raise RuntimeError("Tencent limit prices: 1 batch(es) failed")

        with self.assertRaises(RuntimeError):
            self._run(rows=[], api=failing)


if __name__ == "__main__":
    unittest.main()
