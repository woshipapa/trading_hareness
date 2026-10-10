import struct
import unittest

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


class TdxMicrostructureTests(unittest.TestCase):
    def test_builders_use_expected_opcode_and_payload(self):
        checks = [
            (micro.build_volume_profile_request(1, "600519"), micro.VOLUME_PROFILE),
            (micro.build_history_orders_request(1, "600519", "2026-10-08"), micro.HISTORY_ORDERS),
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

    def test_history_orders_and_auction(self):
        orders = struct.pack("<Hf", 2, 12.34) + encode_price(1234) + encode_price(7) + encode_price(100)
        orders += encode_price(1) + encode_price(8) + encode_price(20)
        rows = micro.parse_history_orders(orders)
        self.assertEqual([row["price"] for row in rows], [12.34, 12.35])
        auction = struct.pack("<HHfIiBB", 1, 9 * 60 + 25, 12.34, 1000, -200, 0, 30)
        row = micro.parse_auction(auction)[0]
        self.assertEqual(row["time"], "09:25:30")
        self.assertEqual(row["unmatched_side"], "S")

    def test_auction_curve_keeps_unconfirmed_quantities_raw_and_ends_before_0925(self):
        body = struct.pack("<HHfIiBB", 1, 9 * 60 + 24, 12.34, 1000, 200, 0, 57)
        row = micro.parse_auction(body)[0]
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
        rows = micro.parse_minute_data(body, "600519")
        self.assertEqual(rows[0]["price"], 10.0)
        self.assertEqual(rows[1]["price"], 10.05)
        self.assertEqual(rows[1]["volume_lots"], 20)
        history = struct.pack("<HII", 1, 0, 0) + encode_price(1000) + encode_price(100000) + encode_price(20)
        self.assertEqual(micro.parse_history_minute_data(history, "600519")[0]["avg"], 10.0)


if __name__ == "__main__":
    unittest.main()
