import struct
import unittest

from app.datasources.sources import tdx_mac


class MacProtocolTests(unittest.TestCase):
    def test_builders_and_market(self):
        self.assertEqual(tdx_mac.market_code("600519.SH"), (1, "600519"))
        self.assertEqual(tdx_mac.exchange_board_code("881376"), 21376)
        self.assertEqual(tdx_mac.exchange_board_code("880761"), 20761)
        self.assertEqual(tdx_mac.exchange_board_code("399001"), 30001)
        self.assertEqual(tdx_mac.exchange_board_code("899001"), 32001)
        self.assertEqual(tdx_mac.exchange_board_code("000001"), 31001)
        self.assertEqual(tdx_mac.exchange_board_code("US0401"), 30401)
        self.assertEqual(tdx_mac.BAR_PERIODS["1m"], 8)
        self.assertEqual(len(tdx_mac.build_handshake()), 2)
        batch = tdx_mac.build_batch_quotes_request([(0, "000001"), (1, "600519")])
        self.assertEqual(
            struct.unpack_from("<H", batch, 10)[0], tdx_mac.OP_BATCH_QUOTES
        )
        self.assertEqual(struct.unpack_from("<H", batch, 32)[0], 2)

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
        self.assertEqual(rows[0]["symbol_name"], "PingAn")

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
        capital = tdx_mac.build_aux_request(
            tdx_mac.OP_CAPITAL_FLOW, 0, "000001", head=2
        )
        board = tdx_mac.build_aux_request(tdx_mac.OP_BELONG_BOARD, 0, "000001")
        self.assertEqual(capital[0], 2)
        self.assertEqual(board[0], 1)
        self.assertIn(b"Stock_ZJLX", capital)
        self.assertIn(b"Stock_GLHQ", board)
        bars = struct.pack("<H12sBHHI", 1, b"600519\0" * 2, 4, 1, 2, 0) + struct.pack(
            "<IIfffffff", 20261009, 34200, 100, 110, 90, 105, 1000, 20, 30
        )
        self.assertEqual(
            len(tdx_mac.parse_bars(bars)), 0
        )  # first MAC row is the pre-close sentinel


if __name__ == "__main__":
    unittest.main()
