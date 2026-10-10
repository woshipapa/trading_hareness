import struct
import unittest

from app.datasources.sources import tdx_ex_market as ex


class TdxExMarketFixtures(unittest.TestCase):
    def test_setup_and_request_frames(self):
        setup = ex.build_setup()
        self.assertEqual(setup[:10].hex(), "01014865000152005200")
        self.assertEqual(struct.unpack_from("<H", setup, 10)[0], ex.COMMANDS["login"])
        self.assertEqual(len(setup), 92)
        self.assertEqual(len(ex.EX_SETUP_PAYLOAD), 80)
        self.assertEqual(
            struct.unpack_from("<H", ex.build_kline(9, 31, "00700", 0, 5), 6)[0], 22
        )
        self.assertEqual(
            struct.unpack_from("<H", ex.build_kline(9, 31, "00700", 0, 5), 8)[0], 22
        )

    def test_categories_and_instruments(self):
        data = bytearray(2 + 64)
        struct.pack_into("<H", data, 0, 1)
        data[2] = 2              # broad type: HK (synthetic row in the layout of delta 2, D6)
        data[3 : 3 + 8] = "港股市场".encode("gbk")
        data[35] = 31            # the market id requests use: HK main board
        data[36:38] = b"HK"
        category = ex.parse_categories(bytes(data))[0]
        self.assertEqual((category["market_id"], category["category"]), (31, 2))
        self.assertEqual(category["market_id_name"], ex.MARKET_IDS[31])
        self.assertEqual(category["category_name"], "hk")
        self.assertEqual(category["abbr"], "HK")

        row = bytearray(6 + 64)
        struct.pack_into("<H", row, 4, 1)
        row[6] = 2
        row[7] = 31
        row[11:16] = b"00700"
        row[20:28] = "腾讯控股".encode("gbk")
        row[37:41] = b"main"
        instrument = ex.parse_instruments(bytes(row))[0]
        self.assertEqual(instrument["market_id"], 31)
        self.assertEqual(instrument["code"], "00700")
        self.assertEqual(instrument["name"], "腾讯控股")
        self.assertEqual(instrument["desc"], "main")

    def test_quote_and_batch_quote_alignment(self):
        single = bytearray(150)
        single[0] = 31
        single[1:6] = b"00700"
        p = 14
        struct.pack_into("<5f", single, p, 300.0, 301.0, 302.0, 299.0, 301.5)
        struct.pack_into("<I", single, p + 20, 10)
        struct.pack_into("<II", single, p + 28, 100, 5)
        struct.pack_into("<II", single, p + 40, 6, 7)
        struct.pack_into("<I", single, p + 48, 8)
        quote = ex._quote(bytes(single))
        self.assertEqual(quote["code"], "00700")
        self.assertAlmostEqual(quote["price"], 301.5)
        self.assertEqual(quote["bid_volume"][0], 0)

        batch = bytearray(314)
        batch[0] = 74
        batch[1:5] = b"AAPL"
        struct.pack_into("<5f", batch, 24, 190.0, 191.0, 192.0, 189.0, 190.5)
        self.assertAlmostEqual(ex._quote(bytes(batch), code_len=23)["price"], 190.5)

    def test_kline_tick_history_and_table_parsers(self):
        body = bytearray(20 + 32)
        struct.pack_into("<H", body, 18, 1)
        struct.pack_into("<I", body, 20, 20261008)
        struct.pack_into("<5fIf", body, 24, 100.0, 110.0, 90.0, 105.0, 12.5, 300, 105.0)
        bars = ex.parse_klines(bytes(body), 9)
        self.assertEqual(bars[0]["datetime"], "2026-10-08")
        self.assertEqual(bars[0]["volume"], 300)
        self.assertAlmostEqual(bars[0]["price"], 105.0)
        self.assertEqual(
            bars[0]["position"], struct.unpack("<I", struct.pack("<f", 12.5))[0]
        )

        chart = bytearray(34 + 18)
        struct.pack_into("<H", chart, 32, 1)
        struct.pack_into("<HffII", chart, 34, 570, 10.0, 9.5, 100, 20)
        self.assertEqual(ex.parse_tick_chart(bytes(chart))[0]["time"], "09:30")
        self.assertEqual(ex.parse_tick_chart(bytes(chart))[0]["open_interest"], 20)

        history = bytearray(42 + 18)
        struct.pack_into("<H", history, 40, 1)
        struct.pack_into("<HffII", history, 42, 571, 11.0, 10.5, 101, 21)
        self.assertEqual(
            ex.parse_history_tick_chart(bytes(history))[0]["time"], "09:31"
        )

        transactions = bytearray(58 + 16)
        struct.pack_into("<H", transactions, 56, 1)
        struct.pack_into("<HIIiH", transactions, 58, 570, 12345, 10, -2, 0)
        tx = ex.parse_history_transactions(bytes(transactions), 31)[0]
        self.assertEqual(tx["price"], 12.345)
        self.assertEqual(tx["action"], "BUY")
        self.assertEqual(tx["zengcang"], -2)

        table = bytearray(172)
        struct.pack_into("<I", table, 35, 7)
        struct.pack_into("<I", table, 161, 2)
        table[169:172] = b"ok\0"
        parsed = ex.parse_table(bytes(table))
        self.assertEqual(parsed, {"start": 7, "count": 2, "content": "ok"})


if __name__ == "__main__":
    unittest.main()
