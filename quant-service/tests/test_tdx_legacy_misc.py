import struct
import unittest
from datetime import date

from app.datasources.sources import tdx_legacy_misc as legacy
from app.datasources.sources import tdx_protocol


def enc(value: int) -> bytes:
    negative, magnitude = value < 0, abs(value)
    out = bytearray([(magnitude & 0x3F) | (0x40 if negative else 0)])
    magnitude >>= 6
    if magnitude:
        out[0] |= 0x80
    while magnitude:
        part = magnitude & 0x7F
        magnitude >>= 7
        out.append(part | (0x80 if magnitude else 0))
    return bytes(out)


class LegacyBuilderTests(unittest.TestCase):
    def test_builders_have_opcode_and_expected_payloads(self):
        request = legacy.build_quotes_list_request(0, 0, 2, 80, True)
        self.assertEqual(request[10:12], struct.pack("<H", legacy.KMSG_QUOTESLIST))
        self.assertEqual(len(request), 12 + 18)
        batch = legacy.build_quotes_batch_request([(0, "000001"), (1, "600519")])
        self.assertEqual(struct.unpack_from("<H", batch, 12)[0], 5)
        self.assertEqual(struct.unpack_from("<H", batch, 20)[0], 2)
        self.assertEqual(legacy.build_index_info_request(1, "600519")[10:12], struct.pack("<H", 0x051D))
        self.assertEqual(legacy.build_history_transaction_request(0x0FB5, 0, "000001", date(2026, 9, 17))[10:12],
                         struct.pack("<H", 0x0FB5))
        self.assertEqual(len(legacy.build_quotes_list_request(0, 0, count=999)), 30)

    def test_category_and_sort_tables_are_explicit(self):
        self.assertEqual(legacy.QUOTE_CATEGORIES["all_a"], 6)
        self.assertEqual(legacy.QUOTE_CATEGORIES["star"], 8)
        self.assertEqual(legacy.QUOTE_SORT_TYPES["amount"], 10)


class LegacyParserTests(unittest.TestCase):
    def test_index_momentum_is_delta_encoded(self):
        body = struct.pack("<H", 3) + enc(4) + enc(-1) + enc(7)
        self.assertEqual(legacy.parse_index_momentum(body), [4, 3, 10])

    def test_index_info_counts_and_order_fixture(self):
        body = struct.pack("<IB6sH", 1, 1, b"000001", 7)
        body += b"".join(enc(value) for value in (1000, 10, 20, 30, 5, 930, 0, 123, 4))
        body += struct.pack("<f", 12.5)
        body += b"".join(enc(value) for value in (0, 0, 99, 0, 0, 0, 12, 8))
        body += b"".join(enc(0) for _ in range(10))
        body += b"".join(enc(value) for value in (25, 3, 40))
        parsed = legacy.parse_index_info(body)
        self.assertEqual((parsed["up_count"], parsed["down_count"]), (12, 8))
        self.assertEqual(parsed["orders"][0]["price"], 0.25)

    def test_transactions_distinguish_direction_and_history_header(self):
        today = struct.pack("<H", 2) + b"\x00" * 4
        for minutes, delta, volume, num, direction in ((570, 1000, 3, 1, 0), (571, -2, 4, 2, 1)):
            today += struct.pack("<H", minutes) + enc(delta) + enc(volume) + enc(num) + struct.pack("<H", direction)
        rows = legacy.parse_history_transaction_data(today, trade_date=date(2026, 9, 17), with_direction=True)
        self.assertEqual([r["action"] for r in rows], ["BUY", "SELL"])
        self.assertEqual(rows[1]["price_raw"], 998)
        self.assertEqual(rows[0]["date"], "2026-09-17")

    def test_current_transaction_0fc5_uses_varint_direction(self):
        body = struct.pack("<H", 2)
        for minutes, delta, volume, num, direction in ((570, 1000, 3, 1, 0), (571, -2, 4, 2, 1)):
            body += struct.pack("<H", minutes) + enc(delta) + enc(volume) + enc(num) + enc(direction) + enc(0)
        rows = legacy.parse_transaction_data(body)
        self.assertEqual([row["action"] for row in rows], ["BUY", "SELL"])
        self.assertEqual(rows[1]["price_raw"], 998)

    def test_transaction_side_codes_match_tdx_protocol(self):
        self.assertEqual(legacy.TRANSACTION_ACTIONS[5], "P")
        self.assertEqual(legacy.TRANSACTION_ACTIONS[8], "A")
        self.assertEqual(legacy.TRANSACTION_ACTIONS[2], "NEUTRAL")

    def test_announcement_and_heartbeat(self):
        heartbeat = b"\x00" * 6 + struct.pack("<I", 20261009)
        self.assertEqual(legacy.parse_heartbeat(heartbeat)["date"], 20261009)
        payload = b"\x01" + struct.pack("<IHHH", 20261231, 5, 3, 4) + b"TitleBobBody"
        parsed = legacy.parse_announcement(payload)
        self.assertTrue(parsed["has_content"])
        self.assertEqual(parsed["title"], "Title")
        self.assertEqual(parsed["content"], "Body")
        self.assertEqual(legacy.parse_exchange_announcement(b"\x02hello")["content"], "hello")

    def test_security_feature_452_rows(self):
        body = struct.pack("<HBIff", 1, 0, 1, 1.5, 2.5)
        rows = legacy.parse_security_feature452(body)
        self.assertEqual(rows, [{"market": 0, "code": "000001", "p1": 1.5, "p2": 2.5}])

    def test_chart_sampling(self):
        body = struct.pack("<H6s", 0, b"000001") + b"\x00" * 26 + struct.pack("<Hf", 2, 11.5) + b"\x00\x00" + struct.pack("<ff", 1.0, 2.0)
        parsed = legacy.parse_chart_sampling(body)
        self.assertEqual(parsed["count"], 2)
        self.assertEqual(parsed["prices"], [1.0, 2.0])


if __name__ == "__main__":
    unittest.main()
