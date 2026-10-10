"""The legacy 0x053e readers, driven by the quotes of the committed closing-book evidence."""

import ast
import asyncio
import inspect
import json
import re
import struct
import unittest
from pathlib import Path
from unittest import mock

from app.datasources.catalog import BINDINGS
from app.datasources.resolver import _normalise_rows
from app.datasources.sources import tdx_protocol, tdx_quotes

BOOK_EVIDENCE = Path(__file__).resolve().parents[2] / "scripts" / "data" / "tdx_quote_order_book_2026-10-10_mac.json"


def encode_price(value: int) -> bytes:
    """Inverse of tdx_protocol.decode_price."""
    negative, magnitude = value < 0, abs(value)
    first = magnitude & 0x3F
    magnitude >>= 6
    out = bytearray([first | (0x40 if negative else 0) | (0x80 if magnitude else 0)])
    while magnitude:
        byte = magnitude & 0x7F
        magnitude >>= 7
        out.append(byte | (0x80 if magnitude else 0))
    return bytes(out)


def evidence_quotes() -> dict[str, tuple]:
    """The tdx lines of the evidence file as {code: (market, code, price, last_close, book fields)}."""
    quotes = {}
    for line in json.loads(BOOK_EVIDENCE.read_text(encoding="utf-8"))["raw_output"]:
        match = re.fullmatch(r"tdx (\d) (\d{6}) price (\S+) last_close (\S+) vol None cur_vol None (\{.*\})", line)
        if match:
            market, code, price, last_close, book = match.groups()
            quotes[code] = (int(market), code, float(price), float(last_close), ast.literal_eval(book))
    return quotes


def quote_body(quotes) -> bytes:
    """A 0x053e answer in parse_quotes' layout: the price in cents, then the previous close and every bid and ask
    as differences from it. The volume is 12345 and the amount the packed float 17.575 whatever the quote."""
    body = struct.pack("<HH", 0, len(quotes))
    for market, code, price, last_close, book in quotes:
        base = round(price * 100)
        body += struct.pack("<B6sH", market, code.encode(), 0)
        body += b"".join(encode_price(value) for value in (base, round(last_close * 100) - base, 0, 0, 0, 0, 0, 12345, 0))
        body += struct.pack("<I", 0x418C999A) + encode_price(0) * 4
        for level in range(1, 6):
            body += b"".join(encode_price(value) for value in (
                round(book[f"bid{level}"] * 100) - base, round(book[f"ask{level}"] * 100) - base,
                book[f"bid_vol{level}"], book[f"ask_vol{level}"]))
        body += struct.pack("<H", 0) + encode_price(0) * 4 + struct.pack("<hH", 0, 0)
    return body


class FakeClient(tdx_protocol.TdxClient):
    """The real quote logic (chunking, parsing, the positional echo check) over one canned answer."""

    def __init__(self, answer: bytes):
        super().__init__("fake-host", 7709)
        self.answer, self.requests = answer, []

    def _exchange(self, request: bytes) -> bytes:
        self.requests.append(request)
        return self.answer


def patched_call(client):
    async def call(operation, **kwargs):
        assert kwargs == {"handshake_profile": "login_one"}
        return operation(client), "h:7709/login_one"
    return mock.patch.object(tdx_protocol, "call", call)


def binding(capability: str):
    return next(item for item in BINDINGS if item.source == "tdx_public" and item.capability == capability)


class BoardIndexQuoteTests(unittest.TestCase):
    def fetch(self, symbols):
        quotes = evidence_quotes()
        client = FakeClient(quote_body([quotes[symbol[:6]] for symbol in symbols]))
        with patched_call(client):
            return asyncio.run(tdx_quotes.fetch_index_quote(symbols=symbols)), client

    def test_rows_carry_prices_a_computed_change_and_raw_volume_and_amount_but_no_book(self):
        evidence, _client = self.fetch(["880005.SH", "880491.SH"])
        self.assertEqual([row["symbol"] for row in evidence.rows], ["880005.SH", "880491.SH"])
        for row in evidence.rows:
            _market, _code, price, last_close, _book = evidence_quotes()[row["symbol"][:6]]
            self.assertEqual(set(row), {"symbol", "last_price", "pre_close", "pct_change", "volume_raw", "amount_raw"})
            self.assertEqual((row["last_price"], row["pre_close"], row["volume_raw"]), (price, last_close, 12345))
            self.assertAlmostEqual(row["pct_change"], (price - last_close) / last_close * 100)
            self.assertAlmostEqual(row["amount_raw"], 17.575, places=3)
        self.assertEqual((evidence.coverage, evidence.warnings), (1.0, ("tdx_host=h:7709/login_one",)))

    def test_any_other_code_is_refused_before_the_network(self):
        with mock.patch.object(tdx_protocol, "call", mock.AsyncMock(side_effect=AssertionError("network"))):
            for symbol in ("999999.SH", "399300.SZ", "600519.SH", "510300.SH", "127045.SZ", "920000.BJ"):
                with self.subTest(symbol=symbol), self.assertRaisesRegex(ValueError, "takes only board"):
                    asyncio.run(tdx_quotes.fetch_index_quote(symbols=["880005.SH", symbol]))
            with self.assertRaisesRegex(ValueError, "must not be empty"):
                asyncio.run(tdx_quotes.fetch_index_quote(symbols=[]))

    def test_the_binding_gives_the_index_code_and_the_prices_but_no_unitless_volume(self):
        evidence, _client = self.fetch(["880005.SH"])
        projected = _normalise_rows(evidence.rows, binding("sector.index_quote"))
        self.assertTrue(projected.canonical)
        row = projected.rows[0]
        self.assertEqual((row["index_code"], row["last_price"]), ("880005.SH", evidence_quotes()["880005"][2]))
        self.assertTrue({"volume", "turnover"}.isdisjoint(row), "no evidence gives their units")


class QuoteBindingTests(unittest.TestCase):
    ADAPTERS = {"sector.index_quote": tdx_quotes.fetch_index_quote}

    def test_each_binding_documents_the_keyword_only_parameters_of_its_adapter(self):
        for capability, adapter in self.ADAPTERS.items():
            item = binding(capability)
            parameters = inspect.signature(adapter).parameters
            self.assertEqual(item.adapter, f"app/datasources/sources/tdx_quotes.py:{adapter.__name__}")
            self.assertEqual(set(parameters), set(item.spec.params), capability)
            self.assertTrue(all(parameter.kind is inspect.Parameter.KEYWORD_ONLY for parameter in parameters.values()), capability)

    def test_the_bindings_stay_unsupported_research_evidence_in_the_clients_batch_size(self):
        for capability in self.ADAPTERS:
            item = binding(capability)
            self.assertEqual((item.status, item.decision_eligible), ("unsupported", False), capability)
            self.assertEqual((item.spec.handshake_profile, item.spec.paging, item.spec.max_batch),
                             ("login_one", "batch", tdx_protocol.MAX_QUOTES_PER_REQUEST), capability)


if __name__ == "__main__":
    unittest.main()
