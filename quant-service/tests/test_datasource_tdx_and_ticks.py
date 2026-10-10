"""TDX wire format, TDX client files and the tick-flow summary."""

import struct
import tempfile
import unittest
import zlib
import importlib
import sys
from pathlib import Path

from app.datasources.derived.tick_flow import Tick, parse_tencent_detail, summarize_ticks, ticks_from_tdx
from app.datasources.sources import tdx_local_files, tdx_protocol


def encode_price(value: int) -> bytes:
    """Inverse of ``decode_price`` (6 bits + sign in the first byte, then 7s)."""
    negative, magnitude = value < 0, abs(value)
    first = magnitude & 0x3F
    magnitude >>= 6
    out = bytearray([first | (0x40 if negative else 0) | (0x80 if magnitude else 0)])
    while magnitude:
        byte = magnitude & 0x7F
        magnitude >>= 7
        out.append(byte | (0x80 if magnitude else 0))
    return bytes(out)


class TdxPrimitiveTests(unittest.TestCase):
    def test_price_varint_round_trip(self):
        for value in (0, 1, -1, 63, 64, -64, 1159, -250, 123456, -987654):
            decoded, position = tdx_protocol.decode_price(encode_price(value) + b"\xff", 0)
            self.assertEqual(decoded, value)
            self.assertEqual(position, len(encode_price(value)))

    def test_request_bytes_match_the_reference_client(self):
        # Captured from pytdx on the owner peer, 2026-09-18.
        self.assertEqual(tdx_protocol.build_bars_request(9, 0, "000001", 0, 50).hex(),
                         "0c01086401011c001c002d050000303030303031090001000000320000000000000000000000")
        self.assertEqual(tdx_protocol.build_quotes_request([(0, "000001")]).hex(),
                         "0c0120630002130013003e050500000000000000010000303030303031")
        self.assertTrue(tdx_protocol.build_history_ticks_request(0, "000001", __import__("datetime").date(2026, 9, 17), 0, 2000)
                        .startswith(bytes.fromhex("0c013001000112001200b50f") + struct.pack("<I", 20260917)))

    def test_history_ticks_parse_with_side_codes(self):
        records = [(9 * 60 + 15, 1168, 0, 8), (9 * 60 + 25, -9, 3739, 0), (9 * 60 + 30, -1, 3498, 1), (15 * 60, 2, 134, 2)]
        body = struct.pack("<H", len(records)) + b"\x00" * 4
        for minutes, delta, volume, side in records:
            body += struct.pack("<H", minutes) + encode_price(delta) + encode_price(volume) + encode_price(side) + encode_price(0)
        ticks = tdx_protocol.parse_ticks(body, history=True)
        self.assertEqual([tick["time"] for tick in ticks], ["09:15", "09:25", "09:30", "15:00"])
        self.assertEqual([tick["price"] for tick in ticks], [11.68, 11.59, 11.58, 11.6])
        self.assertEqual([tick["side"] for tick in ticks], ["A", "B", "S", "N"])

    def test_xdxr_parse_dividend_and_share_change(self):
        header = b"\x00" * 9 + struct.pack("<H", 2)
        dividend = b"\x00" + b"000001" + b"\x00" + struct.pack("<I", 20260924) + bytes([1]) + struct.pack("<ffff", 2.49, 0, 0, 0)
        share = b"\x00" + b"000001" + b"\x00" + struct.pack("<I", 20260916) + bytes([5]) + struct.pack("<IIII", 0, 0, 0, 0)
        rows = tdx_protocol.parse_xdxr(header + dividend + share)
        self.assertEqual(rows[0]["date"], "2026-09-24")
        self.assertAlmostEqual(rows[0]["cash_dividend_per_10"], 2.49, places=5)
        self.assertEqual(rows[1]["category_name"], "股本变化")
        self.assertEqual(rows[1]["float_shares_after_10k"], 0.0)
        self.assertEqual(tdx_protocol.parse_xdxr(b"\x00" * 5), [])


class FakeSocket:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []
        self.buffer = b""

    def sendall(self, data):
        self.sent.append(bytes(data))
        self.buffer += self.responses.pop(0)

    def recv(self, size):
        chunk, self.buffer = self.buffer[:size], self.buffer[size:]
        return chunk

    def close(self):
        pass


def frame(body: bytes, compress: bool = False) -> bytes:
    payload = zlib.compress(body) if compress else body
    return struct.pack("<IIIHH", 0, 0, 0, len(payload), len(body)) + payload


class TdxClientTests(unittest.TestCase):
    def test_generated_host_module_is_optional(self):
        original = sys.modules.get("app.datasources.sources.tdx_hosts")
        sys.modules["app.datasources.sources.tdx_hosts"] = None
        loaded = importlib.reload(tdx_protocol)
        self.assertEqual(loaded._GENERATED_HOSTS, ())
        fake = type(sys)("app.datasources.sources.tdx_hosts")
        fake.HOSTS = (("synthetic", 7709),)
        sys.modules["app.datasources.sources.tdx_hosts"] = fake
        try:
            loaded = importlib.reload(tdx_protocol)
            self.assertEqual(loaded.DEFAULT_HOSTS, fake.HOSTS)
        finally:
            if original is None:
                sys.modules.pop("app.datasources.sources.tdx_hosts", None)
            else:
                sys.modules["app.datasources.sources.tdx_hosts"] = original
            importlib.reload(tdx_protocol)

    def test_handshake_profiles_send_expected_setup_packets(self):
        original = tdx_protocol.socket.create_connection
        try:
            for profile, expected in (("login_one", tdx_protocol._SETUP_COMMANDS[:1]),
                                      ("legacy_3", tdx_protocol._SETUP_COMMANDS)):
                fake = FakeSocket([frame(b"") for _ in expected])
                tdx_protocol.socket.create_connection = lambda *_args, _fake=fake, **_kwargs: _fake
                with tdx_protocol.TdxClient("host", 7709, handshake_profile=profile):
                    pass
                self.assertEqual(fake.sent, list(expected))
        finally:
            tdx_protocol.socket.create_connection = original

    def test_exchange_decompresses_and_pages_ticks_oldest_first(self):
        def page(minute: int, count: int) -> bytes:
            body = struct.pack("<H", count) + b"\x00" * 4
            for index in range(count):
                body += struct.pack("<H", minute) + encode_price(1000 if index == 0 else 0) + encode_price(1) \
                    + encode_price(0) + encode_price(0)
            return body

        newest = page(14 * 60, tdx_protocol.MAX_TICKS_PER_REQUEST)
        oldest = page(9 * 60 + 30, 3)
        client = tdx_protocol.TdxClient("host", 7709)
        client._socket = FakeSocket([frame(newest, compress=True), frame(oldest)])
        rows = client.ticks(0, "000001", __import__("datetime").date(2026, 9, 17))
        self.assertEqual(len(rows), tdx_protocol.MAX_TICKS_PER_REQUEST + 3)
        self.assertEqual(rows[0]["time"], "09:30")
        self.assertEqual(rows[-1]["time"], "14:00")

    def test_call_fails_over_across_hosts(self):
        attempts = []

        class Refusing(tdx_protocol.TdxClient):
            def __enter__(self):
                attempts.append(self.host)
                raise OSError("refused")

        original = tdx_protocol.TdxClient
        tdx_protocol.TdxClient = Refusing
        try:
            with self.assertRaises(tdx_protocol.TdxProtocolError):
                tdx_protocol.call_sync(lambda client: None, hosts=[("a", 1), ("b", 2)])
        finally:
            tdx_protocol.TdxClient = original
        self.assertEqual(attempts, ["a", "b"])

    def test_a_connected_host_falls_back_to_legacy_3_before_the_next_host(self):
        attempts = []

        class ClosesAfterConnect(tdx_protocol.TdxClient):
            def __enter__(self):
                attempts.append((self.host, self.handshake_profile))
                self._connected = True
                raise tdx_protocol.TdxProtocolError("TDX server closed the connection")

        original = tdx_protocol.TdxClient
        tdx_protocol.TdxClient = ClosesAfterConnect
        tdx_protocol._COOLDOWN_UNTIL.clear()
        try:
            with self.assertRaises(tdx_protocol.TdxProtocolError):
                tdx_protocol.call_sync(lambda client: None, hosts=[("a", 1), ("b", 2)])
            self.assertIn(("a", 1), tdx_protocol._COOLDOWN_UNTIL, "a transport failure cools the host down")
        finally:
            tdx_protocol.TdxClient = original
            tdx_protocol._COOLDOWN_UNTIL.clear()
        self.assertEqual(attempts, [("a", "login_one"), ("a", "legacy_3"), ("b", "login_one"), ("b", "legacy_3")])

    def test_receipt_has_profile_and_decode_failure_does_not_cool(self):
        class Refusing(tdx_protocol.TdxClient):
            def __enter__(self):
                raise struct.error("bad response")

        original = tdx_protocol.TdxClient
        tdx_protocol.TdxClient = Refusing
        tdx_protocol._COOLDOWN_UNTIL.clear()
        try:
            with self.assertRaises(tdx_protocol.TdxProtocolError):
                tdx_protocol.call_sync(lambda _client: None, hosts=[("a", 1)], handshake_profile="legacy_3")
            self.assertNotIn(("a", 1), tdx_protocol._COOLDOWN_UNTIL)
        finally:
            tdx_protocol.TdxClient = original

    def test_sweep_uses_a_new_connection_for_each_section(self):
        calls = []

        class FakeClient:
            def __init__(self, host, port, _timeout, *, handshake_profile):
                calls.append((host, port, handshake_profile))
            def __enter__(self): return self
            def __exit__(self, *_args): pass

        original = tdx_protocol.TdxClient
        tdx_protocol.TdxClient = FakeClient
        try:
            result = tdx_protocol.sweep_sync({"quotes": lambda _c: [1], "bars": lambda _c: [2, 3]},
                                             host=("fixed", 7709), handshake_profile="legacy_3")
        finally:
            tdx_protocol.TdxClient = original
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["host"], "fixed:7709")
        self.assertEqual(result["sections"]["bars"]["rows"], 2)

    def test_market_codes(self):
        self.assertEqual(tdx_protocol.market_code("600519.SH"), (1, "600519"))
        self.assertEqual(tdx_protocol.market_code("920819.BJ"), (2, "920819"))
        with self.assertRaises(ValueError):
            tdx_protocol.market_code("600519")


class TdxLocalFileTests(unittest.TestCase):
    def test_day_and_minute_records(self):
        with tempfile.TemporaryDirectory() as root:
            day = Path(root) / "sz000001.day"
            day.write_bytes(struct.pack("<IIIIIfII", 20260918, 1159, 1182, 1156, 1170, 999918140.0, 85303800, 0)
                            + b"\x01\x02")   # a truncated tail record is ignored
            kind, rows = tdx_local_files.read_file(day)
            self.assertEqual(kind, "daily")
            self.assertEqual(rows, [{"ts_code": "000001.SZ", "trade_date": "2026-09-18", "open": 11.59, "high": 11.82,
                                     "low": 11.56, "close": 11.7, "volume_shares": 85303800, "amount_yuan": 999918144.0}])
            minute = Path(root) / "sh600519.lc1"
            packed = (2026 - 2004) * 2048 + 9 * 100 + 18
            minute.write_bytes(struct.pack("<HHfffffII", packed, 9 * 60 + 31, 1262.99, 1263.5, 1262.0, 1263.0,
                                           1.2e7, 9500, 0))
            kind, rows = tdx_local_files.read_file(minute)
            self.assertEqual(kind, "1m")
            self.assertEqual(rows[0]["datetime"], "2026-09-18 09:31:00")
            self.assertEqual(rows[0]["ts_code"], "600519.SH")
            self.assertEqual(rows[0]["volume"], 9500)

    def test_non_equity_files_are_rejected(self):
        self.assertIsNone(tdx_local_files.symbol_from_filename(Path("sh000001.day")))   # index
        self.assertIsNone(tdx_local_files.symbol_from_filename(Path("sz159919.day")))   # ETF
        self.assertEqual(tdx_local_files.symbol_from_filename(Path("bj920819.day")), "920819.BJ")


class TickFlowTests(unittest.TestCase):
    def test_phases_buckets_and_windows(self):
        ticks = [
            Tick("09:15", 11.68, 0, 0, "A"), Tick("09:20", 11.62, 0, 0, "A"),
            Tick("09:25:00", 11.59, 373900, 4333501, "B"),
            Tick("09:30:03", 11.58, 249100, 2884647, "B"),     # super large buy
            Tick("09:31", 11.57, 30000, 347100, "S"),          # large sell
            Tick("10:05", 11.60, 1000, 11600, "N"),            # small neutral
            Tick("14:57", 11.61, 5000, 58050, "S"),
            Tick("15:00", 11.61, 100000, 1161000, "N"),
            Tick("15:10", 11.61, 1000, 11610, "P"),
        ]
        summary = summarize_ticks(ticks)
        self.assertEqual(summary["auction_curve"]["points"], 2)
        self.assertEqual(summary["auction_curve"]["last_before_0920"], 11.68)
        self.assertEqual(summary["opening_auction"]["amount"], 4333501)
        self.assertEqual(summary["closing_auction"]["prints"], 2)
        self.assertEqual(summary["after_hours"]["amount"], 11610)
        self.assertEqual(summary["overall"]["buy_amount"], 2884647)
        self.assertEqual(summary["overall"]["sell_amount"], 347100 + 58050)
        self.assertEqual(summary["by_bucket"]["super_large"]["buy_amount"], 2884647)
        self.assertEqual(summary["large_net_active_amount"], 2884647 - 347100)
        self.assertIn("09:30", summary["by_window"])
        self.assertNotIn("09:00", summary["by_window"])    # auction prints are not flow

    def test_parsers(self):
        text = 'v_detail_data_sz000001=[0,"0/09:25:00/11.59/0.01/3739/4333501/B|1/09:30:00/11.58/-0.01/3498/4046230/S"]'
        ticks = parse_tencent_detail(text)
        self.assertEqual(ticks[0], Tick("09:25:00", 11.59, 373900, 4333501.0, "B"))
        tdx = ticks_from_tdx([{"time": "09:30", "price": 11.58, "volume_lots": 10, "side": "S"}])
        self.assertEqual(tdx[0].amount, 11.58 * 1000)


if __name__ == "__main__":
    unittest.main()
