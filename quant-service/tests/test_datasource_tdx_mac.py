import asyncio
import contextlib
import dataclasses
import importlib.util
import inspect
import io
import os
import struct
import unittest
import zlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from app.datasources.catalog import BINDINGS, CAPABILITIES, TAXONOMIES
from app.datasources import resolver as resolver_module
from app.datasources.contracts import DECLARED, CapabilityEvidence, CapabilityRequest
from app.datasources.resolver import _normalise_rows
from app.datasources.sources import tdx_mac, tdx_mac_fields
from app.datasources.sources.tdx_mac_fields import active_fields, bitmap_for_bits

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def board_item(code: str, name: str) -> bytes:
    item = bytearray(160)
    struct.pack_into("<H", item, 0, 1)
    item[2:8] = code.encode()
    item[24:24 + len(name.encode("gbk"))] = name.encode("gbk")
    return bytes(item)


def board_page(*items: bytes) -> bytes:
    return struct.pack("<HH", 2 * len(items), len(items)) + b"".join(items)


def member_item(market: int, code: str, name: str) -> bytes:
    item = bytearray(68)
    struct.pack_into("<H", item, 0, market)
    item[2:8] = code.encode()
    item[24:24 + len(name.encode("gbk"))] = name.encode("gbk")
    return bytes(item)


def members_page(*items: bytes) -> bytes:
    return bytes(20) + struct.pack("<IH", len(items), len(items)) + b"".join(items)


def dynamic_body(bitmap: bytes, quotes) -> bytes:
    """A dynamic 0x122b/0x122c answer; ``quotes`` are (market, code, name, {field name: value}) and every set bit
    carries a four-byte value, 0 unless given."""
    fields = active_fields(bitmap)
    body = bitmap + struct.pack("<IH", len(quotes), len(quotes))
    for market, code, name, values in quotes:
        body += struct.pack("<H22s44s", market, code.encode(), name.encode("gbk"))
        for field in fields:
            body += struct.pack({"float32": "<f", "int32": "<i", "uint32": "<I"}[field.format], values.get(field.name, 0))
    return body


def requested_stocks(request: bytes) -> list[tuple[int, str]]:
    """The (market, code) pairs of a 0x122b request, which carries a 20-byte bitmap and a count first."""
    count = struct.unpack_from("<H", request, 32)[0]
    return [(struct.unpack_from("<H", request, 34 + 24 * index)[0],
             request[36 + 24 * index:58 + 24 * index].rstrip(b"\0").decode()) for index in range(count)]


def bars_body(bars) -> bytes:
    """A 0x122e answer: the 33-byte header (row count at offset 27) and 36-byte rows of
    (yyyymmdd, seconds, open, high, low, close, amount, volume, float shares)."""
    header = bytearray(33)
    struct.pack_into("<H", header, 27, len(bars))
    return bytes(header) + b"".join(struct.pack("<IIfffffff", *bar) for bar in bars)


class FakeMacClient(tdx_mac.TdxMacClient):
    """The real client logic over canned answers: each request is recorded and answered by its opcode."""

    def __init__(self, answers):
        super().__init__("fake-host")
        self.answers, self.requests = answers, []

    def __enter__(self):
        return self

    def _exchange(self, request: bytes) -> bytes:
        self.requests.append(request)
        return self.answers[struct.unpack_from("<H", request, 10)[0]](request)

    def opcodes(self) -> list[int]:
        return [struct.unpack_from("<H", request, 10)[0] for request in self.requests]


def quote_values():
    """Field values of one 2026-10-09 14:58:57 snapshot (exactly representable as float32)."""
    return {"close": 10.5, "vol": 1234, "vol_ratio": 1.25, "amount": 25000000.0, "turnover": 0.5,
            "buy_price_limit": 11.5, "sell_price_limit": 9.5,
            "server_update_date": 20261009, "server_update_time": 145857}


def quote_answer(values=None, answer_for=lambda stocks: stocks):
    """A 0x122b answer: one row per symbol ``answer_for`` returns for the requested ones, in the requested bitmap."""
    def answer(request):
        return dynamic_body(request[12:32], [
            (market, code, f"name{code}", values or quote_values()) for market, code in answer_for(requested_stocks(request))])
    return answer


def mac_binding(capability: str):
    return next(item for item in BINDINGS if item.source == "tdx_mac" and item.capability == capability)


def schema_fields(capability: str, *, without=()) -> set[str]:
    """The canonical fields the capability promises, so a contract test follows a rename in the catalog."""
    return set(CAPABILITIES[capability].schema.names) - set(without)


def patched_call(client, host="mac-host:7709"):
    async def call(operation, **kwargs):
        return operation(client), host
    return mock.patch.object(tdx_mac, "call", call)


class MacBoardCatalogTests(unittest.TestCase):
    """sector.board_catalog rows are exactly board_code, name and board_type."""

    def catalog_client(self, boards_by_type):
        return FakeMacClient({tdx_mac.OP_BOARD: lambda request: board_page(
            *[board_item(code, name) for code, name in boards_by_type[struct.unpack_from("<H", request, 14)[0]]])})

    def test_adapter_rows_are_board_code_name_and_board_type_and_skip_types_2_and_6(self):
        client = self.catalog_client({0: [("881376", "Coal")], 1: [("881377", "Gas")], 3: [("880710", "Chip")],
                                      4: [("880842", "Bank")], 5: [("880231", "Area")]})
        with patched_call(client):
            evidence = asyncio.run(tdx_mac.fetch_board_catalog())
        self.assertEqual(evidence.rows, [
            {"board_code": "881376", "name": "Coal", "board_type": 0},
            {"board_code": "881377", "name": "Gas", "board_type": 1},
            {"board_code": "880710", "name": "Chip", "board_type": 3},
            {"board_code": "880842", "name": "Bank", "board_type": 4},
            {"board_code": "880231", "name": "Area", "board_type": 5}])
        self.assertEqual(evidence.warnings, ("tdx_host=mac-host:7709",))
        self.assertEqual([struct.unpack_from("<H", request, 14)[0] for request in client.requests], [0, 1, 3, 4, 5])

    def test_a_fixture_through_the_real_binding_keeps_board_code_name_and_board_type(self):
        client = self.catalog_client({0: [("881376", "Coal")], 1: [], 3: [], 4: [], 5: [("880231", "Area")]})
        with patched_call(client):
            evidence = asyncio.run(tdx_mac.fetch_board_catalog())
        projected = _normalise_rows(evidence.rows, mac_binding("sector.board_catalog"))
        self.assertTrue(projected.canonical)
        self.assertEqual((projected.status, projected.warnings), (None, ()))
        self.assertEqual(projected.rows, [{"board_code": "881376", "name": "Coal", "board_type": 0},
                                          {"board_code": "880231", "name": "Area", "board_type": 5}])
        for row in projected.rows:
            self.assertLessEqual(schema_fields("sector.board_catalog"), set(row))


class MacPagingTests(unittest.TestCase):
    def test_a_full_board_page_is_followed_by_the_next_page(self):
        def answer(request):
            start = struct.unpack_from("<H", request, 18)[0]
            return board_page(*[board_item(f"88{start + index:04d}", f"b{start + index}")
                                for index in range(150 if start == 0 else 3)])

        client = FakeMacClient({tdx_mac.OP_BOARD: answer})
        rows = client.board_list(3)
        self.assertEqual((len(rows), rows[0]["code"], rows[149]["code"], rows[-1]["code"]), (153, "880000", "880149", "880152"))
        self.assertEqual([struct.unpack_from("<H", request, 18)[0] for request in client.requests], [0, 150])

    def test_a_short_board_page_ends_the_list(self):
        client = FakeMacClient({tdx_mac.OP_BOARD: lambda request: board_page(board_item("880001", "Coal"))})
        self.assertEqual(len(client.board_list(0)), 1)
        self.assertEqual(len(client.requests), 1)

    def members_client(self, total):
        def answer(request):
            start = struct.unpack_from("<I", request, 27)[0]
            body = bytearray(members_page(*[member_item(1, f"6{start + index:05d}", "m")
                                            for index in range(min(80, total - start))]))
            struct.pack_into("<I", body, 20, total)
            return bytes(body)
        return FakeMacClient({tdx_mac.OP_MEMBERS: answer})

    def test_board_members_page_until_the_announced_total(self):
        client = self.members_client(100)
        rows = client.board_members(20710)
        self.assertEqual((len(rows), rows[0]["symbol"], rows[-1]["symbol"]), (100, "600000", "600099"))
        self.assertEqual([struct.unpack_from("<I", request, 27)[0] for request in client.requests], [0, 80])

    def test_board_members_stop_when_the_first_page_holds_the_total(self):
        client = self.members_client(80)
        self.assertEqual(len(client.board_members(20710)), 80)
        self.assertEqual(len(client.requests), 1)


class MacMembershipTests(unittest.TestCase):
    """sector.membership rows carry the four canonical fields, known_at being the UTC collection time."""

    MEMBERS = {tdx_mac.OP_MEMBERS: lambda request: members_page(
        member_item(1, "600519", "Moutai"), member_item(0, "000001", "PingAn"))}

    def fetch(self, client, **params):
        with patched_call(client):
            return asyncio.run(tdx_mac.fetch_membership(**params))

    def test_adapter_rows_carry_taxonomy_sector_symbol_and_a_utc_known_at(self):
        client = FakeMacClient(self.MEMBERS)
        before = datetime.now(timezone.utc)
        evidence = self.fetch(client, sector_key="880710", board_type=3)
        after = datetime.now(timezone.utc)
        rows = evidence.rows
        self.assertEqual(evidence.warnings, ("tdx_host=mac-host:7709",))
        self.assertEqual([row["symbol"] for row in rows], ["600519.SH", "000001.SZ"])
        for row in rows:
            self.assertEqual(set(row), {"taxonomy_key", "sector_key", "symbol", "known_at"})
            self.assertEqual((row["taxonomy_key"], row["sector_key"]), ("tdx_mac_type_3", "880710"))
            self.assertEqual(row["known_at"].utcoffset(), timedelta(0))
            self.assertTrue(before <= row["known_at"] <= after)

    def test_the_board_type_comes_from_the_catalog_row_and_no_board_list_is_read(self):
        client = FakeMacClient(self.MEMBERS)
        self.fetch(client, sector_key="880710", board_type=3)
        self.assertEqual(client.opcodes(), [tdx_mac.OP_MEMBERS])
        self.assertEqual(struct.unpack_from("<I", client.requests[0], 12)[0], tdx_mac.exchange_board_code("880710"))

    def test_an_unknown_board_type_raises_before_any_network_call_and_names_the_key(self):
        for board_type in (2, 6, 9):
            with mock.patch.object(tdx_mac, "call", mock.AsyncMock(side_effect=AssertionError("network"))):
                with self.assertRaisesRegex(tdx_mac.TdxMacError, f"unknown MAC board key '880710'.*type {board_type}"):
                    asyncio.run(tdx_mac.fetch_membership(sector_key="880710", board_type=board_type))

    def test_a_malformed_board_key_raises_before_any_network_call_and_names_the_key(self):
        for key in ("abc", "HKx", ""):
            with mock.patch.object(tdx_mac, "call", mock.AsyncMock(side_effect=AssertionError("network"))):
                with self.assertRaisesRegex(tdx_mac.TdxMacError, f"unknown MAC board key {key!r}"):
                    asyncio.run(tdx_mac.fetch_membership(sector_key=key, board_type=3))

    def test_an_unknown_market_id_in_the_answer_raises(self):
        client = FakeMacClient({tdx_mac.OP_MEMBERS: lambda request: members_page(member_item(7, "600519", "Moutai"))})
        with self.assertRaisesRegex(tdx_mac.TdxMacError, "unknown market id: 7"):
            self.fetch(client, sector_key="880710", board_type=3)

    def test_a_fixture_through_the_real_binding_keeps_the_four_canonical_fields(self):
        evidence = self.fetch(FakeMacClient(self.MEMBERS), sector_key="880710", board_type=3)
        projected = _normalise_rows(evidence.rows, mac_binding("sector.membership"))
        self.assertTrue(projected.canonical)
        self.assertEqual((projected.status, projected.warnings), (None, ()))
        self.assertEqual({(row["taxonomy_key"], row["sector_key"], row["symbol"]) for row in projected.rows},
                         {("tdx_mac_type_3", "880710", "600519.SH"), ("tdx_mac_type_3", "880710", "000001.SZ")})
        self.assertTrue(all(row["known_at"].utcoffset() == timedelta(0) for row in projected.rows))
        for row in projected.rows:
            self.assertLessEqual(schema_fields("sector.membership"), set(row))

    def test_every_board_type_has_exactly_one_taxonomy(self):
        taxonomies = {key for key, item in TAXONOMIES.items() if item.source == "tdx_mac"}
        self.assertEqual(taxonomies, {f"tdx_mac_type_{board_type}" for board_type in tdx_mac.BOARD_TYPES})
        self.assertEqual(tdx_mac.BOARD_TYPES, (0, 1, 3, 4, 5))


class MacProtocolTests(unittest.TestCase):
    def test_board_codes_follow_gotdx(self):
        for key, code in (("881376", 21376), ("880761", 20761), ("399001", 30001), ("899001", 32001),
                          ("000001", 31001)):
            self.assertEqual(tdx_mac.exchange_board_code(key), code)

    def test_requests_carry_their_opcode_and_count(self):
        self.assertEqual(tdx_mac.BAR_PERIODS["1m"], 8)
        self.assertEqual(len(tdx_mac.build_handshake()), 2)
        batch = tdx_mac.build_batch_quotes_request([(0, "000001"), (1, "600519")])
        self.assertEqual(struct.unpack_from("<H", batch, 10)[0], tdx_mac.OP_BATCH_QUOTES)
        self.assertEqual(struct.unpack_from("<H", batch, 32)[0], 2)

    def test_the_belong_board_and_capital_flow_queries_differ_in_head_and_query(self):
        belong = tdx_mac.build_aux_request(tdx_mac.OP_BELONG_BOARD, 0, "000001")
        flow = tdx_mac_fields.build_capital_flow_request("000001.SZ")
        self.assertEqual((belong[0], flow[0]), (1, 2))
        self.assertEqual({struct.unpack_from("<H", request, 10)[0] for request in (belong, flow)}, {tdx_mac.OP_BELONG_BOARD})
        self.assertIn(b"Stock_GLHQ", belong)
        # the request scripts/probe-tdx-q-flow.py sent when it reconciled 0x38 and 0x6b with 0x1218
        self.assertEqual(flow, tdx_mac.build_request(
            tdx_mac.OP_BELONG_BOARD, struct.pack("<H8s16s21s", 0, b"000001", b"", b"Stock_ZJLX"), head=2))

    def test_member_quote_request_carries_sort_filter_and_the_quote_bit(self):
        request = tdx_mac.build_board_members_request(20812, quotes=True, sort_type=1, sort_order=0, filter_byte=4)
        sort_type = struct.unpack_from("<H", request, 25)[0]
        bitmap = request[35:55]
        self.assertEqual((sort_type, request[33], bitmap[17], bitmap[19] & 1), (1, 0, 4, 1))
        with self.assertRaises(ValueError):
            tdx_mac.build_board_members_request(20812, quotes=True, bitmap=bytes(19))

    def test_helpers_live_in_one_place(self):
        # tdx_protocol owns market_code/symbol/decode_gbk; tdx_mac owns the 0x122c, 0x1218, 0x123d, 0x123e
        # and 0x1237 builders, so neither module keeps a second copy.
        gone = {tdx_mac: ("MARKETS", "market_code", "_text"),
                tdx_mac_fields: ("build_mac_request", "build_belong_board_request", "build_auction_request",
                                 "build_tick_charts_request", "build_market_monitor_request",
                                 "build_board_member_quotes_request", "market_code", "_text", "_f32")}
        for module, names in gone.items():
            for name in names:
                self.assertFalse(hasattr(module, name), f"{module.__name__}.{name}")

    def test_a_board_list_row_decodes_every_field_at_its_offset(self):
        item = bytearray(160)
        struct.pack_into("<H", item, 0, 1)
        item[2:8] = b"880001"
        item[24:29] = b"Coal\0"
        struct.pack_into("<fff", item, 68, 10.5, 0.75, 10.0)
        item[82:88] = b"000001"
        item[104:111] = b"PingAn\0"
        struct.pack_into("<fff", item, 148, 11.5, 1.25, 11.0)
        self.assertEqual(tdx_mac.parse_board_list(struct.pack("<HH", 2, 99) + bytes(item)), [{
            "market": 1, "code": "880001", "name": "Coal", "price": 10.5, "rise_speed": 0.75, "pre_close": 10.0,
            "leading_market": 0, "leading_code": "000001", "leading_name": "PingAn",
            "leading_price": 11.5, "leading_rise_speed": 1.25, "leading_pre_close": 11.0}])

    def test_names_are_read_as_gbk_even_when_the_bytes_are_also_valid_utf8(self):
        # The GBK bytes of 通22转债 are valid UTF-8 too and decode to other characters if UTF-8 is tried first.
        self.assertEqual(tdx_mac.parse_board_list(board_page(board_item("880001", "通22转债")))[0]["name"], "通22转债")
        self.assertEqual(tdx_mac.parse_board_members(members_page(member_item(1, "600519", "贵州茅台")))[0]["name"], "贵州茅台")

    def test_static_member_rows_are_market_symbol_and_name(self):
        rows = tdx_mac.parse_board_members(members_page(member_item(1, "600519", "Moutai"), member_item(0, "000001", "PingAn")))
        self.assertEqual(rows, [{"market": 1, "symbol": "600519", "name": "Moutai"},
                                {"market": 0, "symbol": "000001", "name": "PingAn"}])

    def test_dynamic_rows_decode_for_batch_quotes_and_member_quotes(self):
        body = dynamic_body(bitmap_for_bits([5]), [(1, "600519", "Moutai", {"vol": 123})])
        self.assertEqual(tdx_mac.parse_batch_quotes(body, [(1, "600519")])[0]["vol"], 123)
        self.assertEqual(tdx_mac.parse_board_members(body, quotes=True)[0]["vol"], 123)


class MacWatchSnapshotTests(unittest.TestCase):
    def fetch(self, client, symbols):
        with patched_call(client):
            return asyncio.run(tdx_mac.fetch_watch_snapshot(symbols=symbols))

    def test_rows_carry_the_full_symbol_and_an_aware_shanghai_exchange_time(self):
        client = FakeMacClient({tdx_mac.OP_BATCH_QUOTES: quote_answer()})
        evidence = self.fetch(client, ["000001.SZ", "600519.SH"])
        self.assertEqual([row["symbol"] for row in evidence.rows], ["000001.SZ", "600519.SH"])
        stamp = evidence.rows[0]["exchange_time"]
        self.assertEqual(stamp, datetime(2026, 10, 9, 14, 58, 57, tzinfo=tdx_mac.CN_TZ))
        self.assertEqual((str(stamp.tzinfo), stamp.utcoffset()), ("Asia/Shanghai", timedelta(hours=8)))
        self.assertTrue({"server_update_date", "server_update_time"}.isdisjoint(evidence.rows[0]))
        self.assertEqual(evidence.warnings, ("tdx_host=mac-host:7709",))

    def test_81_symbols_make_two_requests(self):
        symbols = [f"{number:06d}.SZ" for number in range(1, 82)]
        client = FakeMacClient({tdx_mac.OP_BATCH_QUOTES: quote_answer()})
        evidence = self.fetch(client, symbols)
        self.assertEqual([len(requested_stocks(request)) for request in client.requests], [80, 1])
        self.assertEqual([row["symbol"] for row in evidence.rows], symbols)

    def test_a_count_mismatch_raises(self):
        client = FakeMacClient({tdx_mac.OP_BATCH_QUOTES: quote_answer(answer_for=lambda stocks: stocks[:-1])})
        with self.assertRaisesRegex(tdx_mac.TdxMacError, "1 rows for 2 requested"):
            self.fetch(client, ["000001.SZ", "600519.SH"])

    def test_a_row_for_another_code_is_dropped_by_position_and_logged(self):
        # The placeholder repeats the code requested at position 1; a lookup by code would keep it.
        client = FakeMacClient({tdx_mac.OP_BATCH_QUOTES: quote_answer(answer_for=lambda stocks: [stocks[1], stocks[1]])})
        with self.assertLogs("app.datasources.sources.tdx_mac", "WARNING") as logs:
            evidence = self.fetch(client, ["920000.BJ", "600519.SH"])
        self.assertEqual([row["symbol"] for row in evidence.rows], ["600519.SH"])
        self.assertIn("code_mismatch requested=(2, '920000') returned=(1, '600519')", logs.output[0])

    def test_an_invalid_server_date_raises(self):
        client = FakeMacClient({tdx_mac.OP_BATCH_QUOTES: quote_answer({**quote_values(), "server_update_date": 0})})
        with self.assertRaisesRegex(tdx_mac.TdxMacError, "invalid MAC date 0"):
            self.fetch(client, ["000001.SZ"])

    def test_a_fixture_row_through_the_real_binding_yields_price_and_an_aware_exchange_time(self):
        client = FakeMacClient({tdx_mac.OP_BATCH_QUOTES: quote_answer()})
        evidence = self.fetch(client, ["000001.SZ"])
        projected = _normalise_rows(evidence.rows, mac_binding("quote.watch_snapshot"))
        self.assertTrue(projected.canonical)
        self.assertEqual((projected.status, projected.warnings), (None, ()))
        row = projected.rows[0]
        self.assertEqual((row["price"], row["volume"], row["volume_ratio"], row["turnover_rate"], row["amount"]),
                         (10.5, 123400, 1.25, 0.5, 25000000.0))
        self.assertEqual(row["exchange_time"], datetime(2026, 10, 9, 14, 58, 57, tzinfo=tdx_mac.CN_TZ))
        self.assertIsNotNone(row["exchange_time"].utcoffset())
        self.assertLessEqual(schema_fields("quote.watch_snapshot"), set(row))


class MacLimitPriceTests(unittest.TestCase):
    def fetch(self, client, symbols):
        with patched_call(client):
            return asyncio.run(tdx_mac.fetch_limit_prices(symbols=symbols))

    def test_the_limits_bitmap_is_the_date_and_the_two_limit_prices(self):
        self.assertEqual(tdx_mac.LIMITS_BITMAP, bitmap_for_bits([0x13, 0x20, 0x21]))

    def test_rows_carry_the_symbol_the_limits_and_the_trade_date_and_ask_only_for_those_bits(self):
        client = FakeMacClient({tdx_mac.OP_BATCH_QUOTES: quote_answer()})
        evidence = self.fetch(client, ["000001.SZ"])
        self.assertEqual(evidence.rows, [{"symbol": "000001.SZ", "trade_date": date(2026, 10, 9),
                                          "limit_up": 11.5, "limit_down": 9.5}])
        self.assertEqual(client.requests[0][12:32], tdx_mac.LIMITS_BITMAP)

    def test_81_symbols_make_two_requests_and_a_foreign_row_is_dropped(self):
        symbols = [f"{number:06d}.SZ" for number in range(1, 82)]
        client = FakeMacClient({tdx_mac.OP_BATCH_QUOTES: quote_answer(
            answer_for=lambda stocks: [(1, "600839")] + stocks[1:] if len(stocks) == 80 else stocks)})
        with self.assertLogs("app.datasources.sources.tdx_mac", "WARNING"):
            evidence = self.fetch(client, symbols)
        self.assertEqual([len(requested_stocks(request)) for request in client.requests], [80, 1])
        self.assertEqual([row["symbol"] for row in evidence.rows], symbols[1:])

    def test_a_fixture_row_through_the_real_binding_yields_up_limit_down_limit_and_trade_date(self):
        client = FakeMacClient({tdx_mac.OP_BATCH_QUOTES: quote_answer()})
        evidence = self.fetch(client, ["000001.SZ"])
        projected = _normalise_rows(evidence.rows, mac_binding("limits.prices"))
        self.assertTrue(projected.canonical)
        self.assertEqual((projected.status, projected.warnings), (None, ()))
        row = projected.rows[0]
        self.assertEqual((row["up_limit"], row["down_limit"], row["trade_date"]), (11.5, 9.5, date(2026, 10, 9)))
        self.assertLessEqual(schema_fields("limits.prices"), set(row))


class MacBindingSpecTests(unittest.TestCase):
    ADAPTERS = {"quote.watch_snapshot": tdx_mac.fetch_watch_snapshot, "limits.prices": tdx_mac.fetch_limit_prices,
                "sector.board_catalog": tdx_mac.fetch_board_catalog, "sector.membership": tdx_mac.fetch_membership,
                "bars.daily": tdx_mac.fetch_daily_bars, "bars.minute": tdx_mac.fetch_minute_bars}

    def test_every_adapter_takes_exactly_the_keyword_only_parameters_its_binding_documents(self):
        for capability, adapter in self.ADAPTERS.items():
            binding = mac_binding(capability)
            parameters = inspect.signature(adapter).parameters
            self.assertEqual(binding.adapter, f"app/datasources/sources/tdx_mac.py:{adapter.__name__}")
            self.assertEqual(set(parameters), set(binding.spec.params), capability)
            self.assertTrue(all(item.kind is inspect.Parameter.KEYWORD_ONLY for item in parameters.values()), capability)

    def test_all_six_bindings_stay_unsupported_research_evidence(self):
        self.assertEqual({item.capability for item in BINDINGS if item.source == "tdx_mac"}, set(self.ADAPTERS))
        for capability in self.ADAPTERS:
            binding = mac_binding(capability)
            self.assertEqual((binding.status, binding.decision_eligible), ("unsupported", False), capability)
            self.assertEqual(binding.spec.handshake_profile, "mac", capability)

    def test_batch_bindings_use_the_clients_batch_size(self):
        for capability in ("quote.watch_snapshot", "limits.prices"):
            self.assertEqual(mac_binding(capability).spec.max_batch, tdx_mac.MAX_BATCH)


SENTINEL = (20261008, 0, 99.0, 99.0, 99.0, 100.0, 500.0, 50.0, 9.0)
BARS = [
    (20261009, 34200, 100.0, 110.0, 90.0, 105.0, 1000.0, 2000.0, 30.0),
    (20261009, 34261, 105.0, 111.0, 101.0, 108.0, 2000.0, 4000.0, 30.0),
    (20261009, 34320, 108.0, 112.0, 104.0, 109.0, 3000.0, 6000.0, 30.0),
]


class MacResolverTests(unittest.TestCase):
    """The adapters behind the resolver, as if their bindings were promoted from UNSUPPORTED."""

    CASES = {
        "quote.watch_snapshot": (tdx_mac.fetch_watch_snapshot, {"symbols": ["000001.SZ"]}, ()),
        "limits.prices": (tdx_mac.fetch_limit_prices, {"symbols": ["000001.SZ"]}, ()),
        "sector.board_catalog": (tdx_mac.fetch_board_catalog, {}, ()),
        "sector.membership": (tdx_mac.fetch_membership, {"sector_key": "880710", "board_type": 3}, ()),
        "bars.daily": (tdx_mac.fetch_daily_bars, {"symbol": "000001.SZ", "count": 3}, ("pre_close",)),
        "bars.minute": (tdx_mac.fetch_minute_bars, {"symbol": "000001.SZ", "count": 3}, ()),
    }

    def test_every_adapter_passes_the_resolver_with_all_its_canonical_fields_required(self):
        client = FakeMacClient({
            tdx_mac.OP_BATCH_QUOTES: quote_answer(),
            tdx_mac.OP_BOARD: lambda request: board_page(board_item("881376", "Coal")),
            tdx_mac.OP_MEMBERS: lambda request: members_page(member_item(1, "600519", "Moutai")),
            tdx_mac.OP_BARS: lambda request: bars_body([SENTINEL, *BARS]),
        })
        for capability, (adapter, params, without) in self.CASES.items():
            binding = dataclasses.replace(mac_binding(capability), status=DECLARED)
            request = CapabilityRequest(capability=capability,
                                        required_fields=tuple(sorted(schema_fields(capability, without=without))))
            with patched_call(client), mock.patch.object(resolver_module, "bindings_for", lambda *a, **k: [binding]):
                resolver = resolver_module.CapabilityResolver()
                resolver.bind("tdx_mac", capability, adapter)
                result = asyncio.run(resolver.fetch(capability, request=request, **params))
            self.assertEqual((result.quality.status, result.quality.schema), ("complete", "canonical"), capability)
            self.assertTrue(result.rows, capability)


class MacBarAdapterTests(unittest.TestCase):
    def fetch(self, adapter, bars=BARS, **params):
        client = FakeMacClient({tdx_mac.OP_BARS: lambda request: bars_body([SENTINEL, *bars])})
        with patched_call(client):
            return asyncio.run(adapter(**params)), client.requests

    def test_minute_rows_carry_the_symbol_and_an_aware_bar_time_for_every_bar(self):
        evidence, requests = self.fetch(tdx_mac.fetch_minute_bars, symbol="000001.SZ", count=3)
        shanghai = tdx_mac.CN_TZ
        self.assertEqual(evidence.rows, [
            {"symbol": "000001.SZ", "bar_time": datetime(2026, 10, 9, 9, 30, tzinfo=shanghai),
             "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0, "amount": 1000.0, "volume": 2000.0},
            {"symbol": "000001.SZ", "bar_time": datetime(2026, 10, 9, 9, 31, 1, tzinfo=shanghai),
             "open": 105.0, "high": 111.0, "low": 101.0, "close": 108.0, "amount": 2000.0, "volume": 4000.0},
            {"symbol": "000001.SZ", "bar_time": datetime(2026, 10, 9, 9, 32, tzinfo=shanghai),
             "open": 108.0, "high": 112.0, "low": 104.0, "close": 109.0, "amount": 3000.0, "volume": 6000.0},
        ])
        self.assertEqual(evidence.warnings, ("tdx_host=mac-host:7709",))
        self.assertEqual(str(evidence.rows[0]["bar_time"].tzinfo), "Asia/Shanghai")
        # one-minute period, and one more bar than asked for because the first is the sentinel
        self.assertEqual([(struct.unpack_from("<H", request, 36)[0], struct.unpack_from("<H", request, 44)[0])
                          for request in requests], [(tdx_mac.BAR_PERIODS["1m"], 4)])

    def test_a_bar_time_beyond_the_day_raises(self):
        with self.assertRaisesRegex(tdx_mac.TdxMacError, "invalid MAC time 24:00:00"):
            self.fetch(tdx_mac.fetch_minute_bars, bars=[(20261009, 86400, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)],
                       symbol="000001.SZ", count=1)

    def test_daily_rows_carry_the_symbol_and_every_bar(self):
        evidence, requests = self.fetch(tdx_mac.fetch_daily_bars, symbol="600519.SH", count=3)
        self.assertEqual([(row["symbol"], row["date"], row["close"], row["volume"]) for row in evidence.rows],
                         [("600519.SH", "2026-10-09", 105.0, 2000.0), ("600519.SH", "2026-10-09", 108.0, 4000.0),
                          ("600519.SH", "2026-10-09", 109.0, 6000.0)])
        self.assertEqual([(struct.unpack_from("<H", request, 36)[0], struct.unpack_from("<H", request, 44)[0])
                          for request in requests], [(tdx_mac.BAR_PERIODS["1d"], 4)])

    def test_a_daily_fixture_through_the_real_binding_yields_lots(self):
        evidence, _ = self.fetch(tdx_mac.fetch_daily_bars, symbol="600519.SH", count=3)
        projected = _normalise_rows(evidence.rows, mac_binding("bars.daily"))
        self.assertTrue(projected.canonical)
        self.assertEqual((projected.status, projected.warnings), (None, ()))
        self.assertEqual([row["close"] for row in projected.rows], [105.0, 108.0, 109.0])
        for row, shares in zip(projected.rows, (2000.0, 4000.0, 6000.0)):
            self.assertAlmostEqual(row["volume"], shares / 100)
            # the MAC answer carries no pre_close of its own, so the binding promises the rest
            self.assertLessEqual(schema_fields("bars.daily", without=("pre_close",)), set(row))

    def test_a_minute_fixture_through_the_real_binding_keeps_shares_and_the_bar_time(self):
        evidence, _ = self.fetch(tdx_mac.fetch_minute_bars, symbol="000001.SZ", count=3)
        projected = _normalise_rows(evidence.rows, mac_binding("bars.minute"))
        self.assertTrue(projected.canonical)
        self.assertEqual((projected.status, projected.warnings), (None, ()))
        self.assertEqual([(row["bar_time"], row["volume"]) for row in projected.rows],
                         [(datetime(2026, 10, 9, 9, 30, 0, tzinfo=tdx_mac.CN_TZ), 2000.0),
                          (datetime(2026, 10, 9, 9, 31, 1, tzinfo=tdx_mac.CN_TZ), 4000.0),
                          (datetime(2026, 10, 9, 9, 32, 0, tzinfo=tdx_mac.CN_TZ), 6000.0)])
        for row in projected.rows:
            self.assertLessEqual(schema_fields("bars.minute"), set(row))

    def test_the_minute_binding_says_why_it_stays_unsupported(self):
        binding = mac_binding("bars.minute")
        self.assertEqual(binding.status, "unsupported")
        self.assertFalse(binding.decision_eligible)
        self.assertIn("source_available_at", binding.notes)
        self.assertIn("UNSUPPORTED", binding.notes)
        self.assertIn("available=none", binding.spec.time_semantics)


class MacEvidenceTests(unittest.TestCase):
    def test_every_adapter_returns_evidence_that_names_the_answering_host(self):
        client = FakeMacClient({
            tdx_mac.OP_BATCH_QUOTES: quote_answer(),
            tdx_mac.OP_BOARD: lambda request: board_page(board_item("881376", "Coal")),
            tdx_mac.OP_MEMBERS: lambda request: members_page(member_item(1, "600519", "Moutai")),
            tdx_mac.OP_BARS: lambda request: bars_body([SENTINEL, *BARS]),
        })

        async def run_all():
            return [await tdx_mac.fetch_watch_snapshot(symbols=["000001.SZ"]),
                    await tdx_mac.fetch_limit_prices(symbols=["000001.SZ"]),
                    await tdx_mac.fetch_board_catalog(),
                    await tdx_mac.fetch_membership(sector_key="880710", board_type=3),
                    await tdx_mac.fetch_daily_bars(symbol="000001.SZ", count=3),
                    await tdx_mac.fetch_minute_bars(symbol="000001.SZ", count=3)]

        with patched_call(client, host="second-host:7709"):
            results = asyncio.run(run_all())
        self.assertEqual(len(results), len(MacBindingSpecTests.ADAPTERS))
        for evidence in results:
            self.assertIsInstance(evidence, CapabilityEvidence)
            self.assertEqual(evidence.warnings, ("tdx_host=second-host:7709",))
            self.assertIsNotNone(evidence.available_at_min)
            self.assertTrue(evidence.rows)


class MacBarParserTests(unittest.TestCase):
    def test_the_first_row_is_the_sentinel_and_every_other_row_is_kept(self):
        body = bars_body([
            (20261008, 0, 99.0, 99.0, 99.0, 100.0, 500.0, 50.0, 9.0),
            (20261009, 34200, 100.0, 110.0, 90.0, 105.0, 1000.0, 20.0, 30.0),
            (20261012, 34260, 105.0, 111.0, 101.0, 108.0, 2000.0, 40.0, 30.0),
            (20261013, 34320, 108.0, 112.0, 104.0, 109.0, 3000.0, 60.0, 30.0),
        ])
        rows = tdx_mac.parse_bars(body)
        self.assertEqual(
            [(row["date"], row["seconds"], row["open"], row["close"], row["amount"], row["volume"]) for row in rows],
            [("2026-10-09", 34200, 100.0, 105.0, 1000.0, 20.0),
             ("2026-10-12", 34260, 105.0, 108.0, 2000.0, 40.0),
             ("2026-10-13", 34320, 108.0, 109.0, 3000.0, 60.0)])

    def test_the_sentinel_row_is_not_decoded(self):
        # The row that is dropped may carry no valid date; it must not fail the answer.
        body = bars_body([(0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0), (20261009, 34200, 1.0, 2.0, 0.5, 1.5, 3.0, 4.0, 5.0)])
        self.assertEqual([row["date"] for row in tdx_mac.parse_bars(body)], ["2026-10-09"])
        with self.assertRaisesRegex(tdx_mac.TdxMacError, "invalid MAC date 0"):
            tdx_mac.parse_bars(bars_body([(20261009, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0), (0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)]))

    def test_a_header_only_answer_has_no_rows(self):
        self.assertEqual(tdx_mac.parse_bars(bars_body([])), [])

    def test_a_short_or_truncated_answer_raises(self):
        with self.assertRaises(tdx_mac.TdxMacError):
            tdx_mac.parse_bars(bytes(32))
        with self.assertRaises(tdx_mac.TdxMacError):
            tdx_mac.parse_bars(bars_body([(20261009, 0, 1, 1, 1, 1, 1, 1, 1)] * 3)[:-1])

    def test_a_short_board_list_answer_raises(self):
        with self.assertRaises(tdx_mac.TdxMacError):
            tdx_mac.parse_board_list(bytes(3))
        self.assertEqual(tdx_mac.parse_board_list(board_page()), [])


def mac_frame(body: bytes, compressed: bool = False) -> bytes:
    """A MAC answer: a 16-byte header with the sent and plain sizes at offset 12, then the body."""
    sent = zlib.compress(body) if compressed else body
    return struct.pack("<IIIHH", 0, 0, 0, len(sent), len(body)) + sent


class FakeSocket:
    """Answers each request with the next canned reply."""

    def __init__(self, replies):
        self.replies, self.pending = list(replies), b""

    def sendall(self, data: bytes) -> None:
        self.pending += self.replies.pop(0)

    def recv(self, size: int) -> bytes:
        chunk, self.pending = self.pending[:size], self.pending[size:]
        return chunk

    def close(self) -> None:
        pass


class MacFailoverTests(unittest.TestCase):
    HANDSHAKE = [mac_frame(b"ok"), mac_frame(b"ok")]
    CORRUPT = struct.pack("<IIIHH", 0, 0, 0, 10, 99) + b"not zlib!!"

    def call(self, replies_by_host, operation):
        sockets = {host: FakeSocket(self.HANDSHAKE + replies) for host, replies in replies_by_host.items()}
        with mock.patch.object(tdx_mac.socket, "create_connection", lambda address, timeout: sockets[address[0]]):
            return tdx_mac.call_sync(operation, hosts=[(host, 7709) for host in replies_by_host])

    def test_a_corrupt_compressed_answer_moves_on_to_the_next_host(self):
        good = mac_frame(board_page(board_item("881376", "Coal")), compressed=True)
        rows, host = self.call({"a": [self.CORRUPT], "b": [good]}, lambda client: client.board_list(0))
        self.assertEqual((host, [row["code"] for row in rows]), ("b:7709", ["881376"]))

    def test_a_truncated_answer_moves_on_to_the_next_host(self):
        truncated = mac_frame(board_page(board_item("881376", "Coal"))[:-1])
        good = mac_frame(board_page(board_item("880710", "Gas")))
        rows, host = self.call({"a": [truncated], "b": [good]}, lambda client: client.board_list(0))
        self.assertEqual((host, [row["code"] for row in rows]), ("b:7709", ["880710"]))

    def test_every_host_failing_raises_with_each_cause(self):
        truncated = mac_frame(board_page(board_item("881376", "Coal"))[:-1])
        with self.assertRaises(tdx_mac.TdxMacError) as caught:
            self.call({"a": [self.CORRUPT], "b": [truncated]}, lambda client: client.board_list(0))
        message = str(caught.exception)
        self.assertIn("a:TdxMacError: MAC response failed to decompress", message)
        self.assertIn("b:TdxMacError: truncated board list item 0", message)

    def test_a_refused_connection_moves_on_to_the_next_host(self):
        def create_connection(address, timeout):
            if address[0] == "down":
                raise OSError("connection refused")
            return FakeSocket(self.HANDSHAKE + [mac_frame(board_page(board_item("881376", "Coal")))])

        with mock.patch.object(tdx_mac.socket, "create_connection", create_connection):
            rows, host = tdx_mac.call_sync(lambda client: client.board_list(0), hosts=[("down", 7709), ("up", 7709)])
        self.assertEqual((host, len(rows)), ("up:7709", 1))

    def test_an_adapter_fails_over_through_the_real_transport_and_names_the_host_that_answered(self):
        quotes = dynamic_body(tdx_mac.LIMITS_BITMAP, [(0, "000001", "PingAn", quote_values())])
        sockets = {"a": FakeSocket(self.HANDSHAKE + [self.CORRUPT]),
                   "b": FakeSocket(self.HANDSHAKE + [mac_frame(quotes, compressed=True)])}
        with mock.patch.dict(os.environ, {"TDX_MAC_HOSTS": "a:7709,b:7709"}), \
                mock.patch.object(tdx_mac.socket, "create_connection", lambda address, timeout: sockets[address[0]]):
            evidence = asyncio.run(tdx_mac.fetch_limit_prices(symbols=["000001.SZ"]))
        self.assertEqual(evidence.warnings, ("tdx_host=b:7709",))
        self.assertEqual(evidence.rows, [{"symbol": "000001.SZ", "trade_date": date(2026, 10, 9),
                                          "limit_up": 11.5, "limit_down": 9.5}])

    def test_a_caller_error_is_not_a_host_failure(self):
        def operation(client):
            raise ValueError("symbol must end with .SH, .SZ or .BJ")

        with self.assertRaises(ValueError):
            self.call({"a": []}, operation)


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProbeScriptTests(unittest.TestCase):
    """The probe scripts are code too: a round deleted a constant that one of them still used."""

    def test_verify_tdx_mac_runs_every_probe_it_lists_against_the_client(self):
        script = load_script("verify-tdx-mac")
        client = FakeMacClient({
            tdx_mac.OP_BOARD: lambda request: board_page(board_item("881376", "Coal")),
            tdx_mac.OP_MEMBERS: lambda request: (
                dynamic_body(request[35:55], [(1, "600519", "Moutai", {})]) if request[-1] & 1
                else members_page(member_item(1, "600519", "Moutai"))),
            tdx_mac.OP_BATCH_QUOTES: lambda request: dynamic_body(
                request[12:32], [(market, code, "n", {}) for market, code in requested_stocks(request)]),
            tdx_mac.OP_BARS: lambda request: bars_body([(20261009, 0, 1, 1, 1, 1, 1, 1, 1)] * 6),
            tdx_mac.OP_AUCTION: lambda request: bytes(24) + struct.pack("<I", 58) + bytes(8),
            tdx_mac.OP_TICK_CHARTS: lambda request: bytes(69) + struct.pack("<H", 5) + bytes(2),
            tdx_mac.OP_MARKET_MONITOR: lambda request: struct.pack("<H", 500),
            tdx_mac.OP_BELONG_BOARD: lambda request: bytes(27) + b'[["a"], ["b"]]',
        })

        def call_sync(operation, **kwargs):
            return operation(client), "mac-host:7709"

        out = io.StringIO()
        with mock.patch.object(tdx_mac, "call_sync", call_sync), contextlib.redirect_stdout(out):
            status = script.main()
        report = out.getvalue()
        self.assertEqual(status, 0, report)
        for name in ("auction", "tick_charts", "market_monitor", "belong_board"):
            self.assertRegex(report, rf"{name}: \d+ rows")
        self.assertNotIn("error:", report)
        self.assertNotIn("capital_flow", report)

    def test_verify_tdx_mac_fields_fixture_mode_needs_no_client(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = load_script("verify-tdx-mac-fields").run_fixture()
        self.assertEqual(status, 0)
        self.assertIn("fixture dynamic rows=160", out.getvalue())

    def test_verify_tdx_mac_fields_live_run_goes_through_the_module_client(self):
        script = load_script("verify-tdx-mac-fields")

        def plain(request):
            return bytes(10)  # any answer longer than two bytes is usable

        client = FakeMacClient({
            tdx_mac.OP_BATCH_QUOTES: lambda request: dynamic_body(
                request[12:32], [(market, code, "n", {}) for market, code in requested_stocks(request)]),
            tdx_mac.OP_MEMBERS: lambda request: dynamic_body(request[35:55], [(1, "600519", "Moutai", {})]),
            0x122A: plain, 0x122F: plain, 0x120F: plain, tdx_mac.OP_BELONG_BOARD: plain,
            tdx_mac.OP_AUCTION: plain, tdx_mac.OP_TICK_CHARTS: plain, tdx_mac.OP_MARKET_MONITOR: plain,
        })
        out = io.StringIO()
        with mock.patch.object(tdx_mac, "TdxMacClient", lambda host, port, timeout: client), \
                contextlib.redirect_stdout(out):
            status = script.run_live(5.0)
        report = out.getvalue()
        self.assertEqual(status, 0, report)
        for opcode in (0x122A, 0x122F, 0x120F, 0x1218, 0x123D, 0x123E, 0x1237):
            self.assertIn(f"0x{opcode:04x},bytes=10,MATCH", report)
        variants = [request for request in client.requests if struct.unpack_from("<H", request, 10)[0] == tdx_mac.OP_MEMBERS]
        self.assertEqual([(struct.unpack_from("<H", request, 25)[0], request[33], request[35 + 17]) for request in variants],
                         [(14, 1, 0), (14, 0, 0), (1, 1, 0), (14, 1, 1), (14, 1, 2), (14, 1, 4)])


if __name__ == "__main__":
    unittest.main()
