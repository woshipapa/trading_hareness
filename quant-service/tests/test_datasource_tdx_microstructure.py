import asyncio
import inspect
import struct
import unittest
from unittest import mock

from app.datasources.catalog import BINDINGS
from app.datasources.sources import tdx_microstructure as micro


def encode_price(value: int) -> bytes:
    negative, magnitude = value < 0, abs(value)
    out = bytearray([(magnitude & 0x3F) | (0x40 if negative else 0)])
    magnitude >>= 6
    if magnitude:
        out[0] |= 0x80
    while magnitude:
        byte = magnitude & 0x7F
        magnitude >>= 7
        out.append(byte | (0x80 if magnitude else 0))
    return bytes(out)



def unusual_record(event_type: int, payload: bytes, market: int = 1, code: bytes = b"600000") -> bytes:
    """One 32-byte 0x0563 row: market, code, event type at 9, sequence, 13-byte payload at 15, time."""
    assert len(payload) == 13
    return (struct.pack("<H6sBBBHH", market, code, 0, event_type, 0, 12, 0) + payload
            + struct.pack("<BBH", 0, 9, 3015))


def top_board_body(size: int = 1) -> bytes:
    body = bytes([size])
    for _ in range(9 * size):
        body += bytes([1]) + b"600000" + struct.pack("<ff", 12.34, 1.23)
    return body


def volume_profile_body() -> bytes:
    """A 0x051a answer: the quote header (close 12.34) and two profile rows."""
    body = struct.pack("<HB6sH", 2, 1, b"600519", 7)
    body += b"".join(encode_price(v) for v in (1234, -10, 5, 20, -30, 100, 15, 10000, 500))
    body += struct.pack("<f", 123456.5)
    body += b"".join(encode_price(v) for v in (300, 400, 50, 60))
    for i in range(3):
        body += b"".join(encode_price(v) for v in (i + 1, i + 2, 100 + i, 200 + i))
    body += struct.pack("<H", 42)
    return body + b"".join(encode_price(v) for v in (1234, 100, 40, 60, 2, 20, 8, 12))


def minute_series_body() -> bytes:
    """A 0x0fb4 answer of two minutes."""
    body = struct.pack("<Hf", 2, 12.34) + encode_price(1234) + encode_price(7) + encode_price(100)
    return body + encode_price(1) + encode_price(8) + encode_price(20)


def auction_body() -> bytes:
    """A 0x056a answer of one row."""
    return struct.pack("<HHfIiBB", 1, 9 * 60 + 24, 12.34, 1000, -200, 0, 57)


class RecordingClient:
    """Answers by opcode and keeps the requests."""

    def __init__(self, answers):
        self.answers, self.requests = answers, []

    def _exchange(self, request):
        self.requests.append(request)
        return self.answers[struct.unpack_from("<H", request, 10)[0]]


def patched_call(client):
    async def call(operation, **kwargs):
        return operation(client), "h:7709/login_one"
    return mock.patch.object(micro.tdx_protocol, "call", call)


class SymbolReaderTests(unittest.TestCase):
    """The three readers that take a symbol: volume profile, minute series and auction curve."""

    def readers(self, symbol):
        return [micro.fetch_volume_profile(symbol=symbol), micro.fetch_minute_series(symbol=symbol, trade_date="2026-10-09"),
                micro.fetch_auction_curve(symbol=symbol)]

    def test_an_old_bj_code_is_requested_as_its_920_code_and_every_row_carries_the_new_symbol(self):
        client = RecordingClient({micro.VOLUME_PROFILE: volume_profile_body(), micro.MINUTE_SERIES: minute_series_body(),
                                  micro.AUCTION: auction_body()})

        async def run_all():
            return [await reader for reader in self.readers("430017.BJ")]

        with patched_call(client):
            profile, series, auction = asyncio.run(run_all())
        for evidence in (profile, series, auction):
            self.assertEqual({row["symbol"] for row in evidence.rows}, {"920017.BJ"})
            self.assertEqual(evidence.warnings, ("tdx_host=h:7709/login_one",))
        volume_profile, minute_series, auction_curve = client.requests
        self.assertEqual((struct.unpack_from("<H", volume_profile, 12)[0], volume_profile[14:20]), (2, b"920017"))
        self.assertEqual((minute_series[16], minute_series[17:23]), (2, b"920017"))
        self.assertEqual((struct.unpack_from("<H", auction_curve, 12)[0], auction_curve[14:20]), (2, b"920017"))

    def test_the_readers_that_decode_integer_prices_take_stocks_only(self):
        client = RecordingClient({micro.VOLUME_PROFILE: volume_profile_body(), micro.MINUTE_SERIES: minute_series_body()})
        with patched_call(client):
            for symbol in ("600519.SH", "300750.SZ", "688981.SH", "920000.BJ"):  # main board, ChiNext, STAR, BJ
                profile = asyncio.run(micro.fetch_volume_profile(symbol=symbol))
                series = asyncio.run(micro.fetch_minute_series(symbol=symbol, trade_date="2026-10-09"))
                self.assertEqual({row["symbol"] for row in profile.rows + series.rows}, {symbol})
        with mock.patch.object(micro.tdx_protocol, "call", mock.AsyncMock(side_effect=AssertionError("network"))):
            for symbol in ("510300.SH", "159915.SZ", "127045.SZ", "113709.SH", "999999.SH", "880005.SH", "500001.SH",
                           "161121.SZ"):  # ETFs, convertible bonds, an index, a board, a fund and a LOF
                for reader in (micro.fetch_volume_profile(symbol=symbol),
                               micro.fetch_minute_series(symbol=symbol, trade_date="2026-10-09")):
                    with self.subTest(symbol=symbol), self.assertRaisesRegex(ValueError, "takes only stock_"):
                        asyncio.run(reader)

    def test_a_symbol_that_is_not_six_digits_and_an_exchange_is_refused_before_the_network(self):
        with mock.patch.object(micro.tdx_protocol, "call", mock.AsyncMock(side_effect=AssertionError("network"))):
            for symbol in ("600519", "60051.SH", "600519.XX"):
                for reader in self.readers(symbol):
                    with self.subTest(symbol=symbol), self.assertRaises(ValueError):
                        asyncio.run(reader)


class MicrostructureBindingTests(unittest.TestCase):
    ADAPTERS = {"microstructure.volume_profile": micro.fetch_volume_profile, "microstructure.minute_series": micro.fetch_minute_series,
                "microstructure.auction_curve": micro.fetch_auction_curve, "microstructure.unusual": micro.fetch_unusual,
                "microstructure.top_board": micro.fetch_top_board}

    def test_every_adapter_takes_exactly_the_keyword_only_parameters_its_binding_documents(self):
        for capability, adapter in self.ADAPTERS.items():
            item = next(binding for binding in BINDINGS if binding.source == "tdx_public" and binding.capability == capability)
            parameters = inspect.signature(adapter).parameters
            self.assertEqual(item.adapter, f"app/datasources/sources/tdx_microstructure.py:{adapter.__name__}")
            self.assertEqual(set(parameters), set(item.spec.params), capability)
            self.assertTrue(all(parameter.kind is inspect.Parameter.KEYWORD_ONLY for parameter in parameters.values()), capability)


class TdxMicrostructureTests(unittest.TestCase):
    def test_builders_use_expected_opcode_and_payload(self):
        checks = [
            (micro.build_volume_profile_request(1, "600519"), micro.VOLUME_PROFILE),
            (micro.build_minute_series_request(1, "600519", "2026-10-08"), micro.MINUTE_SERIES),
            (micro.build_auction_request(1, "600519"), micro.AUCTION),
            (micro.build_unusual_request(0, 2, 3), micro.UNUSUAL),
            (micro.build_top_board_request(6, 4), micro.TOP_BOARD),
            (micro.build_minute_data_request(1, "600519"), micro.MINUTE_TIME_DATA),
            (micro.build_history_minute_data_request(1, "600519", "2026-10-08"), micro.HISTORY_MINUTE_TIME_DATA),
        ]
        for request, opcode in checks:
            self.assertEqual(struct.unpack_from("<H", request, 10)[0], opcode)
            self.assertEqual(request[0], 0x0C)
        self.assertEqual(struct.unpack_from("<i", checks[-1][0], 12)[0], -20261008)

    def test_volume_profile(self):
        body = struct.pack("<HB6sH", 2, 1, b"600519", 7)
        body += b"".join(encode_price(v) for v in (1234, -10, 5, 20, -30, 100, 15, 10000, 500))
        body += struct.pack("<f", 123456.5)
        body += b"".join(encode_price(v) for v in (300, 400, 50, 60))
        for i in range(3):
            body += b"".join(encode_price(v) for v in (i + 1, i + 2, 100 + i, 200 + i))
        body += struct.pack("<H", 42)
        body += b"".join(encode_price(v) for v in (1234, 100, 40, 60, 2, 20, 8, 12))
        result = micro.parse_volume_profile(body)
        self.assertEqual(result["code"], "600519")
        self.assertAlmostEqual(result["close"], 12.34)
        self.assertEqual(result["profiles"][1]["price"], 12.36)

    def test_minute_series_and_auction(self):
        series = struct.pack("<Hf", 2, 12.34) + encode_price(1234) + encode_price(7) + encode_price(100)
        series += encode_price(1) + encode_price(8) + encode_price(20)
        rows = micro.parse_minute_series(series)
        self.assertEqual([row["price"] for row in rows], [12.34, 12.35])
        auction = struct.pack("<HHfIiBB", 1, 9 * 60 + 25, 12.34, 1000, -200, 0, 30)
        row = micro.parse_auction(auction)[0]
        self.assertEqual(row["time"], "09:25:30")
        self.assertEqual(row["unmatched_side"], "S")

    def test_auction_curve_keeps_unconfirmed_quantities_raw_and_ends_before_0925(self):
        body = struct.pack("<HHfIiBB", 1, 9 * 60 + 24, 12.34, 1000, 200, 0, 57)
        rows = micro.parse_auction(body)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["time"], "09:24:57")
        self.assertEqual(row["matched_raw"], 1000)
        self.assertEqual(row["unmatched_raw"], 200)
        self.assertNotIn("matched_shares", row)
        self.assertNotIn("unmatched_shares", row)

    def test_unusual_and_top_board(self):
        record = struct.pack("<H6sBBBHHBfffBBH", 1, b"600000", 0, 4, 0, 12, 0, 0,
                             0.0123, 0.0, 0.0, 0, 9, 3015)
        unusual = micro.parse_unusual(struct.pack("<H", 1) + record)[0]
        self.assertEqual(unusual["description"], "加速拉升")
        self.assertEqual(unusual["time"], "09:30:15")
        board = bytes([1])
        for _ in range(9):
            board += bytes([1]) + b"600000" + struct.pack("<ff", 12.34, 1.23)
        parsed = micro.parse_top_board(board)
        self.assertEqual(parsed["increase"][0]["code"], "600000")
        self.assertAlmostEqual(parsed["turnover"][0]["value"], 1.23)

    def test_minute_and_history_minute(self):
        body = struct.pack("<HH", 2, 0)
        body += encode_price(1000) + encode_price(100000) + encode_price(10)
        body += encode_price(5) + encode_price(100) + encode_price(20)
        rows = micro.parse_minute_data(body)
        self.assertEqual(rows[0]["price"], 10.0)
        self.assertEqual(rows[1]["price"], 10.05)
        self.assertEqual(rows[1]["volume_lots"], 20)
        history = struct.pack("<HII", 1, 0, 0) + encode_price(1000) + encode_price(100000) + encode_price(20)
        self.assertEqual(micro.parse_history_minute_data(history)[0]["average"], 10.0)

    def test_profile_delta_normalization_detects_mutation(self):
        body = struct.pack("<HB6sH", 2, 1, b"600519", 7)
        body += b"".join(encode_price(v) for v in (1234, -10, 5, 20, -30, 100, 15, 10000, 500))
        body += struct.pack("<f", 123456.5)
        body += b"".join(encode_price(v) for v in (300, 400, 50, 60))
        for i in range(3):
            body += b"".join(encode_price(v) for v in (i + 1, i + 2, 100 + i, 200 + i))
        body += struct.pack("<H", 42)
        body += b"".join(encode_price(v) for v in (1234, 100, 40, 60, 2, 20, 8, 12))
        result = micro.parse_volume_profile(body)
        self.assertAlmostEqual(result["profiles"][0]["price"], 12.34)
        self.assertNotEqual(result["profiles"][0]["price"], 12.35)

    def test_minute_prices_are_cumulative(self):
        body = struct.pack("<HH", 2, 0)
        body += encode_price(1000) + encode_price(100000) + encode_price(10)
        body += encode_price(5) + encode_price(100) + encode_price(20)
        rows = micro.parse_minute_data(body)
        self.assertEqual(rows[1]["price"], 10.05)
        self.assertEqual(rows[0]["price"], 10.0)

    def test_pre_close_is_preserved(self):
        series = struct.pack("<Hf", 2, 12.34) + encode_price(1234) + encode_price(7) + encode_price(100)
        series += encode_price(1) + encode_price(8) + encode_price(20)
        rows = micro.parse_minute_series(series)
        self.assertNotEqual(rows[0]["pre_close"], 0.0)
        self.assertAlmostEqual(rows[0]["pre_close"], 12.34, places=5)

    def test_volume_profile_bid_ask_asymmetry(self):
        body = struct.pack("<HB6sH", 2, 1, b"600519", 7)
        body += b"".join(encode_price(v) for v in (1234, -10, 5, 20, -30, 100, 15, 10000, 500))
        body += struct.pack("<f", 123456.5)
        body += b"".join(encode_price(v) for v in (300, 400, 50, 60))
        for i in range(3):
            body += b"".join(encode_price(v) for v in (i + 1, i + 2, 100 + i, 200 + i))
        body += struct.pack("<H", 42)
        body += b"".join(encode_price(v) for v in (1234, 100, 40, 60, 2, 20, 8, 12))
        result = micro.parse_volume_profile(body)
        self.assertNotEqual(result["bid_levels"][0]["price"], result["ask_levels"][0]["price"])

    def test_auction_no_invented_0925_row(self):
        body = struct.pack("<HHfIiBB", 1, 9 * 60 + 24, 12.34, 1000, 200, 0, 57)
        rows = micro.parse_auction(body)
        self.assertEqual(len(rows), 1)
        times = [row["time"] for row in rows]
        self.assertNotIn("09:25:00", times)

    def test_limit_events_decode_direction_and_subtype_as_gotdx(self):
        sealed_up = micro.parse_unusual(struct.pack("<H", 1) + unusual_record(
            0x14, b"\x00\x02" + struct.pack("<ff", 12.34, 5000.0) + bytes(3)))[0]
        self.assertEqual((sealed_up["description"], sealed_up["value"]), ("封涨停板", "12.34/5000.00"))
        self.assertNotIn("payload_raw", sealed_up)
        opened_down = micro.parse_unusual(struct.pack("<H", 1) + unusual_record(
            0x14, b"\x01\x05" + struct.pack("<ff", 9.87, 10.0) + bytes(3)))[0]
        self.assertEqual(opened_down["description"], "打开跌停")
        unknown_sub = micro.parse_unusual(struct.pack("<H", 1) + unusual_record(
            0x14, b"\x00\x03" + bytes(11)))[0]
        self.assertEqual(unknown_sub["description"], "unknown_0x14_03")
        self.assertEqual(unknown_sub["payload_raw"], "0003" + "00" * 11)

    def test_close_events_decode_by_first_byte(self):
        pulled_up = micro.parse_unusual(struct.pack("<H", 1) + unusual_record(
            0x15, b"\x02" + struct.pack("<fff", 0.0125, 3.5, 0.0)))[0]
        self.assertEqual((pulled_up["description"], pulled_up["value"]), ("尾盘拉升", "1.25%/3.50"))
        unnamed = micro.parse_unusual(struct.pack("<H", 1) + unusual_record(
            0x15, b"\x00" + struct.pack("<fff", 0.0125, 3.5, 0.0)))[0]
        self.assertEqual(unnamed["description"], "unknown_0x15_00")
        self.assertIn("payload_raw", unnamed)

    def test_unknown_event_types_keep_their_payload(self):
        payload = bytes(range(13))
        row = micro.parse_unusual(struct.pack("<H", 1) + unusual_record(0x1F, payload))[0]
        self.assertEqual((row["description"], row["payload_raw"]), ("unknown_0x1f", payload.hex()))

    def test_minute_series_skips_the_lunch_break(self):
        body = struct.pack("<Hf", 240, 12.34) + encode_price(1234) + encode_price(0) + encode_price(1)
        body += (encode_price(0) * 3) * 239
        times = [row["time"] for row in micro.parse_minute_series(body)]
        self.assertEqual((times[0], times[119], times[120], times[239]), ("09:31:00", "11:30:00", "13:01:00", "15:00:00"))

    def test_adapters_add_symbols_and_the_answering_host(self):
        answers = {micro.TOP_BOARD: top_board_body(), micro.UNUSUAL: struct.pack("<H", 1) + unusual_record(
            0x1F, bytes(13), market=0, code=b"000001")}

        class Client:
            def _exchange(self, request):
                return answers[struct.unpack_from("<H", request, 10)[0]]

        async def call(operation, **kwargs):
            self.assertEqual(kwargs, {"handshake_profile": "login_one"})
            return operation(Client()), "h:7709/login_one"

        with mock.patch.object(micro.tdx_protocol, "call", call):
            board = asyncio.run(micro.fetch_top_board(category=0, size=1))
            events = asyncio.run(micro.fetch_unusual(market=0))
        self.assertEqual(len(board.rows), 9)
        self.assertEqual({row["category"] for row in board.rows}, {
            "increase", "decrease", "amplitude", "rise_speed", "fall_speed", "volume_ratio",
            "positive_commission_ratio", "negative_commission_ratio", "turnover"})
        self.assertEqual(board.rows[0]["symbol"], "600000.SH")
        self.assertEqual(events.rows[0]["symbol"], "000001.SZ")
        self.assertEqual(board.warnings, ("tdx_host=h:7709/login_one",))

if __name__ == "__main__":
    unittest.main()
