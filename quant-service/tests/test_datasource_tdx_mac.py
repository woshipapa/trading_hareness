import asyncio
import contextlib
import importlib.util
import io
import struct
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from app.datasources.sources import tdx_mac, tdx_mac_fields
from app.datasources.sources.tdx_mac_fields import active_fields

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def board_item(code: str, name: str) -> bytes:
    item = bytearray(160)
    struct.pack_into("<H", item, 0, 1)
    item[2:8] = code.encode()
    item[24:24 + len(name)] = name.encode()
    return bytes(item)


def board_page(*items: bytes) -> bytes:
    return struct.pack("<HH", 2 * len(items), len(items)) + b"".join(items)


def member_item(market: int, code: str, name: str) -> bytes:
    item = bytearray(68)
    struct.pack_into("<H", item, 0, market)
    item[2:8] = code.encode()
    item[24:24 + len(name)] = name.encode()
    return bytes(item)


def members_page(*items: bytes) -> bytes:
    return bytes(20) + struct.pack("<IH", len(items), len(items)) + b"".join(items)


def dynamic_body(bitmap: bytes, quotes) -> bytes:
    """A dynamic 0x122b/0x122c answer; ``quotes`` are (market, code, name, {field name: value}) and every set bit
    carries a four-byte value, 0 unless given."""
    fields = active_fields(bitmap)
    body = bitmap + struct.pack("<IH", len(quotes), len(quotes))
    for market, code, name, values in quotes:
        body += struct.pack("<H22s44s", market, code.encode(), name.encode())
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


def patched_call(client, host="mac-host:7709"):
    async def call(operation, **kwargs):
        return operation(client), host
    return mock.patch.object(tdx_mac, "call", call)


class MacAdapterSignatureTests(unittest.TestCase):
    """Test that adapters accept canonical keyword-only parameters."""

    def test_fetch_watch_snapshot_accepts_symbols_keyword(self):
        """fetch_watch_snapshot must accept symbols as keyword-only parameter."""
        import inspect
        sig = inspect.signature(tdx_mac.fetch_watch_snapshot)
        # Check that symbols is keyword-only and no positional args before it
        self.assertIn('symbols', sig.parameters)
        self.assertEqual(sig.parameters['symbols'].kind, inspect.Parameter.KEYWORD_ONLY)

    def test_fetch_limit_prices_accepts_symbols_keyword(self):
        """fetch_limit_prices must accept symbols as keyword-only parameter."""
        import inspect
        sig = inspect.signature(tdx_mac.fetch_limit_prices)
        self.assertIn('symbols', sig.parameters)
        self.assertEqual(sig.parameters['symbols'].kind, inspect.Parameter.KEYWORD_ONLY)

    def test_fetch_daily_bars_accepts_symbol_count_keywords(self):
        """fetch_daily_bars must accept symbol and count as keyword-only parameters."""
        import inspect
        sig = inspect.signature(tdx_mac.fetch_daily_bars)
        self.assertIn('symbol', sig.parameters)
        self.assertIn('count', sig.parameters)
        self.assertEqual(sig.parameters['symbol'].kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertEqual(sig.parameters['count'].kind, inspect.Parameter.KEYWORD_ONLY)

    def test_fetch_minute_bars_accepts_symbol_count_keywords(self):
        """fetch_minute_bars must accept symbol and count as keyword-only parameters."""
        import inspect
        sig = inspect.signature(tdx_mac.fetch_minute_bars)
        self.assertIn('symbol', sig.parameters)
        self.assertIn('count', sig.parameters)
        self.assertEqual(sig.parameters['symbol'].kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertEqual(sig.parameters['count'].kind, inspect.Parameter.KEYWORD_ONLY)

    def test_fetch_board_catalog_has_no_parameters(self):
        """fetch_board_catalog must have no required parameters."""
        import inspect
        sig = inspect.signature(tdx_mac.fetch_board_catalog)
        # Should have no required parameters (no *args, **kwargs in signature)
        required_params = [p for p in sig.parameters.values()
                          if p.default == inspect.Parameter.empty
                          and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)]
        self.assertEqual(len(required_params), 0)

    def test_fetch_membership_accepts_sector_key_keyword(self):
        """fetch_membership must accept sector_key as keyword-only parameter."""
        import inspect
        sig = inspect.signature(tdx_mac.fetch_membership)
        self.assertIn('sector_key', sig.parameters)
        self.assertEqual(sig.parameters['sector_key'].kind, inspect.Parameter.KEYWORD_ONLY)


class MacBoardCatalogTests(unittest.TestCase):
    """Test sector.board_catalog returns rows with singular field names."""

    def test_board_catalog_field_names(self):
        """Parsed board_catalog rows must have singular field names: board_code, name, board_type."""
        # Synthetic board row - count_all=2 produces 1 row
        item = bytearray(160)
        struct.pack_into("<H", item, 0, 1)  # market
        item[2:8] = b"880001"
        item[24:29] = b"Sector\0"
        struct.pack_into("<f", item, 148, 11.59)  # leading_price (not member_count)
        body = struct.pack("<HH", 2, 99) + bytes(item)

        rows = tdx_mac.parse_board_list(body)
        self.assertEqual(len(rows), 1)
        row = rows[0]

        # Check that these fields exist (will be mapped to board_code, name, board_type in fetch_board_catalog)
        self.assertIn("code", row)
        self.assertEqual(row["code"], "880001")
        self.assertIn("name", row)
        self.assertNotIn("member_count", row)
        # leading_price at offset 148 should be 11.59 (not read as <H which would be 28836)
        self.assertAlmostEqual(row["leading_price"], 11.59, places=2)


class MacMembershipTests(unittest.TestCase):
    """sector.membership rows carry the four canonical fields, known_at being the UTC collection time."""

    def test_adapter_rows_carry_taxonomy_sector_symbol_and_a_utc_known_at(self):
        client = FakeMacClient({
            tdx_mac.OP_BOARD: lambda request: board_page(board_item("881376", "Coal")),
            tdx_mac.OP_MEMBERS: lambda request: members_page(
                member_item(1, "600519", "Moutai"), member_item(0, "000001", "PingAn")),
        })
        before = datetime.now(timezone.utc)
        with patched_call(client):
            rows = asyncio.run(tdx_mac.fetch_membership(sector_key="881376"))
        after = datetime.now(timezone.utc)
        self.assertEqual([row["symbol"] for row in rows], ["600519.SH", "000001.SZ"])
        for row in rows:
            self.assertEqual(set(row), {"taxonomy_key", "sector_key", "symbol", "known_at"})
            self.assertEqual((row["taxonomy_key"], row["sector_key"]), ("tdx_mac_type_0", "881376"))
            self.assertEqual(row["known_at"].utcoffset(), timedelta(0))
            self.assertTrue(before <= row["known_at"] <= after)


class MacProtocolTests(unittest.TestCase):
    def test_builders_and_market(self):
        self.assertEqual(tdx_mac.exchange_board_code("881376"), 21376)
        self.assertEqual(tdx_mac.exchange_board_code("880761"), 20761)
        self.assertEqual(tdx_mac.exchange_board_code("399001"), 30001)
        self.assertEqual(tdx_mac.exchange_board_code("899001"), 32001)
        self.assertEqual(tdx_mac.exchange_board_code("000001"), 31001)
        self.assertEqual(tdx_mac.BAR_PERIODS["1m"], 8)
        self.assertEqual(len(tdx_mac.build_handshake()), 2)
        batch = tdx_mac.build_batch_quotes_request([(0, "000001"), (1, "600519")])
        self.assertEqual(
            struct.unpack_from("<H", batch, 10)[0], tdx_mac.OP_BATCH_QUOTES
        )
        self.assertEqual(struct.unpack_from("<H", batch, 32)[0], 2)

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

    def test_board_list_fixture(self):
        item = bytearray(160)
        struct.pack_into("<H", item, 0, 1)
        item[2:8] = b"880001"
        item[24:29] = b"Coal\0"
        struct.pack_into("<fff", item, 68, 10.5, 0.8, 10.0)
        item[82:88] = b"000001"
        item[104:111] = b"PingAn\0"
        body = struct.pack("<HH", 2, 99) + bytes(item)
        rows = tdx_mac.parse_board_list(body)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "880001")
        self.assertEqual(rows[0]["leading_name"], "PingAn")

    def test_members_quotes_batch_and_bars(self):
        member = bytearray(68)
        member[2:8] = b"600519"
        member[24:31] = b"Moutai\0"
        members = b"\0" * 24 + struct.pack("<H", 1) + bytes(member)
        self.assertEqual(tdx_mac.parse_board_members(members)[0]["symbol"], "600519")
        bitmap = bytes([1 << 5]) + b"\0" * 19
        batch = (
            bitmap
            + struct.pack("<IH", 1, 1)
            + struct.pack("<H22s", 1, b"600519" + b"\0" * 16)
            + b"\0" * 44
            + struct.pack("<I", 123)
        )
        self.assertEqual(tdx_mac.parse_batch_quotes(batch)[0]["vol"], 123)
        dynamic = (
            bitmap
            + struct.pack("<IH", 1, 1)
            + struct.pack("<H22s", 1, b"600519" + b"\0" * 16)
            + b"\0" * 44
            + struct.pack("<I", 123)
        )
        self.assertEqual(
            tdx_mac.parse_board_members(dynamic, quotes=True)[0]["vol"], 123
        )
        board = tdx_mac.build_aux_request(tdx_mac.OP_BELONG_BOARD, 0, "000001")
        self.assertEqual(board[0], 1)
        self.assertIn(b"Stock_GLHQ", board)
        bars = struct.pack("<H12sBHHI", 1, b"600519\0" * 2, 4, 1, 2, 0) + struct.pack(
            "<IIfffffff", 20261009, 34200, 100, 110, 90, 105, 1000, 20, 30
        )
        self.assertEqual(
            len(tdx_mac.parse_bars(bars)), 0
        )  # first MAC row is the pre-close sentinel


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
