"""Exchange-published limit prices from Tencent quotes (decision 0005, row "当日涨跌停价")."""

from __future__ import annotations

import asyncio
import unittest
from datetime import date

from app.datasources.sources.tencent_limits import PROVIDER_KEY, session_limit_cross_section
from app.longhu_vendor_source import parse_tencent_quote_text, tencent_quote_key


def quote_text(code: str, name: str, price: float, pre_close: float, clock: str, up: str, down: str) -> str:
    """One Tencent ``v_`` line laid out as qt.gtimg.cn returned it on 2026-10-09 (88 fields)."""
    fields = [""] * 88
    fields[1], fields[2], fields[3], fields[4] = name, code[2:], str(price), str(pre_close)
    fields[30], fields[47], fields[48] = clock, up, down
    return f'v_{code}="{"~".join(fields)}";\n'


class ParserTests(unittest.TestCase):
    def test_fields_47_and_48_are_the_published_limits(self):
        text = (quote_text("sh600519", "贵州茅台", 1268.99, 1255.79, "20261009103559", "1381.37", "1130.21")
                + quote_text("bj920438", "戈碧迦", 96.9, 98.85, "20261009103554", "128.50", "69.20"))
        rows = {row["ts_code"]: row for row in parse_tencent_quote_text(
            text, {"sh600519": "600519.SH", "bj920438": "920438.BJ"})}
        self.assertEqual((rows["600519.SH"]["up_limit"], rows["600519.SH"]["down_limit"]), (1381.37, 1130.21))
        self.assertEqual(rows["920438.BJ"]["up_limit"], 128.5)
        self.assertEqual(rows["600519.SH"]["trade_date"], "20261009")

    def test_a_missing_or_zero_limit_is_none_not_zero(self):
        text = quote_text("sh600519", "贵州茅台", 1268.99, 1255.79, "20261009103559", "0.00", "")
        row = parse_tencent_quote_text(text, {"sh600519": "600519.SH"})[0]
        self.assertIsNone(row["up_limit"])
        self.assertIsNone(row["down_limit"])

    def test_request_keys_carry_the_exchange(self):
        self.assertEqual([tencent_quote_key(code) for code in ("600519.SH", "300750.SZ", "920438.BJ")],
                         ["sh600519", "sz300750", "bj920438"])


def quotes(*rows, errors=()):
    async def fetch(symbols):
        return list(rows), {"requested": len(symbols), "received": len(rows), "errors": list(errors)}
    return fetch


async def universe(symbols):
    return symbols


def run(fetch, symbols=("600519.SH", "300750.SZ")):
    return asyncio.run(session_limit_cross_section(
        date(2026, 10, 9), universe_symbols=lambda: universe(list(symbols)), fetch_quotes=fetch))


SH = {"ts_code": "600519.SH", "trade_date": "20261009", "up_limit": 1381.37, "down_limit": 1130.21, "pre_close": 1255.79}
SZ = {"ts_code": "300750.SZ", "trade_date": "20261009", "up_limit": 344.1, "down_limit": 229.4, "pre_close": 286.75}


class SessionLimitTests(unittest.TestCase):
    def test_a_complete_session_is_returned_in_the_stk_limit_shape(self):
        rows, provider = run(quotes(SH, SZ))
        self.assertEqual(provider, PROVIDER_KEY)
        self.assertEqual([(row["ts_code"], row["trade_date"], row["up_limit"]) for row in rows],
                         [("600519.SH", "20261009", 1381.37), ("300750.SZ", "20261009", 344.1)])

    def test_a_failed_batch_raises_so_nothing_partial_is_stored(self):
        with self.assertRaisesRegex(RuntimeError, "1 batch"):
            run(quotes(SH, SZ, errors=["batch=3:ReadTimeout:timed out"]))

    def test_quotes_still_dated_the_previous_session_are_refused(self):
        stale = [{**SH, "trade_date": "20261008"}, {**SZ, "trade_date": "20261008"}]
        with self.assertRaisesRegex(RuntimeError, "0 of 2 symbols"):
            run(quotes(*stale))

    def test_a_short_answer_is_refused(self):
        symbols = [f"60{index:04d}.SH" for index in range(100)]
        rows = [{**SH, "ts_code": symbol} for symbol in symbols[:90]]
        with self.assertRaisesRegex(RuntimeError, "90 of 100"):
            run(quotes(*rows), symbols=symbols)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
