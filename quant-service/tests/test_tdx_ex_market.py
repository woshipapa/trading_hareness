import asyncio
import struct
import unittest
from unittest import mock

from app.datasources.catalog import bindings_for
from app.datasources.contracts import BINDING_STATES
from app.datasources.resolver import _normalise_rows
from app.datasources.sources import tdx_ex_market as ex


def live_quote(market, code, prices, volumes, open_interest, best_bid, best_ask):
    """Re-encode a quote of the 2026-10-10 live run (scripts/data/tdx_ex_market_verify_2026-10-10_mac.jsonl).

    That run decoded the order book four bytes early, so its bid[0] float is the open-interest word and
    its bid[1]/ask[1] are the real best levels; this puts every recovered value back at its byte offset.
    """
    body = bytearray(14 + 136)
    body[0] = market
    body[1:1 + len(code)] = code.encode()
    struct.pack_into("<5f", body, 14, *prices)
    struct.pack_into("<2I", body, 14 + 28, *volumes)
    struct.pack_into("<I", body, 14 + 52, open_interest)
    struct.pack_into("<fI", body, 14 + 56, best_bid[0], 0)
    struct.pack_into("<I", body, 14 + 76, best_bid[1])
    struct.pack_into("<f", body, 14 + 96, best_ask[0])
    struct.pack_into("<I", body, 14 + 116, best_ask[1])
    return bytes(body)


IF2610 = live_quote(47, "IF2610", (4294.0, 4291.2, 4323.8, 4233.6, 4307.8), (36158, 1), 50002, (4307.6, 1), (4307.8, 2))
AAPL = live_quote(74, "AAPL", (340.42, 331.695, 338.61, 330.70, 336.64), (37881199, 0), 0, (335.55, 201), (336.09, 287))


def bars(*items):
    """A kline answer: 20-byte header with the count at 18, then 32-byte bars."""
    body = bytearray(20)
    struct.pack_into("<H", body, 18, len(items))
    for day, ohlc, slot20, volume, last in items:
        body += struct.pack("<I4f", day, *ohlc) + slot20 + struct.pack("<If", volume, last)
    return bytes(body)


# Live run bars: IF2610 (market 47, category 4) and 00700 (market 31, category 9).
IF_BARS = bars((20261008, (4351.8, 4382.8, 4277.0, 4304.0), struct.pack("<I", 50020), 27842, 4294.0),
               (20261009, (4291.2, 4323.8, 4233.6, 4307.8), struct.pack("<I", 50002), 36158, 4313.6))
HK_BARS = bars((20261009, (415.0, 425.8, 414.8, 424.8), struct.pack("<f", 8610387968.0), 2042, 2350900.0))


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

    def test_quote_offsets_follow_gotdx_on_the_live_quotes(self):
        future, stock = ex._quote(IF2610), ex._quote(AAPL)
        self.assertEqual((future["market_id"], future["code"]), (47, "IF2610"))
        self.assertAlmostEqual(future["pre_close"], 4294.0, places=2)
        self.assertAlmostEqual(future["open"], 4291.2, places=2)
        self.assertAlmostEqual(future["price"], 4307.8, places=2)
        self.assertEqual((future["volume"], future["current_volume"], future["open_interest"]), (36158, 1, 50002))
        self.assertAlmostEqual(future["bid"][0], 4307.6, places=2)
        self.assertAlmostEqual(future["ask"][0], 4307.8, places=2)
        self.assertEqual((future["bid_volume"][0], future["ask_volume"][0]), (1, 2))
        self.assertNotIn("open_interest", stock)
        self.assertAlmostEqual(stock["bid"][0], 335.55, places=2)
        self.assertAlmostEqual(stock["ask"][0], 336.09, places=2)
        self.assertEqual((stock["bid_volume"][0], stock["ask_volume"][0]), (201, 287))

    def test_futures_bars_hold_open_interest_and_settlement(self):
        rows = ex.parse_klines(IF_BARS, 4, 47, "IF2610")
        self.assertEqual([row["datetime"] for row in rows], ["2026-10-08", "2026-10-09"])
        self.assertEqual([(row["open"], row["high"], row["low"], row["close"]) for row in rows][0],
                         tuple(struct.unpack("<4f", struct.pack("<4f", 4351.8, 4382.8, 4277.0, 4304.0))))
        self.assertEqual([row["open_interest"] for row in rows], [50020, 50002])
        self.assertEqual(rows[0]["settlement"], 4294.0, "the next quote's prior settlement")
        self.assertEqual(rows[1]["volume_raw"], 36158)
        self.assertNotIn("amount", rows[0])

    def test_stock_bars_hold_amount(self):
        (row,) = ex.parse_klines(HK_BARS, 9, 31, "00700")
        self.assertEqual((row["market_id"], row["code"], row["volume_raw"]), (31, "00700", 2042))
        self.assertEqual(row["amount"], 8610387968.0)
        self.assertNotIn("open_interest", row)
        self.assertNotIn("settlement", row)

    def test_short_answers_raise(self):
        with self.assertRaises(ex.TdxExMarketError):
            ex._quote(IF2610[:100])
        with self.assertRaises(ex.TdxExMarketError):
            ex.parse_klines(IF_BARS[:-1], 4, 47, "IF2610")
        with self.assertRaises(ex.TdxExMarketError):
            ex.parse_count(b"\0" * 22)
        two_categories = struct.pack("<H", 2) + bytes(64)
        with self.assertRaises(ex.TdxExMarketError):
            ex.parse_categories(two_categories)

    def test_adapters_pick_the_daily_category_and_report_the_host(self):
        sent = []

        class Client:
            def klines(self, category, market_id, code):
                sent.append((category, market_id, code))
                return ex.parse_klines(IF_BARS if market_id == 47 else HK_BARS, category, market_id, code)

        def call_sync(operation, *, hosts):
            return operation(Client()), "h:7727"

        with mock.patch.object(ex, "call_sync", call_sync):
            future = asyncio.run(ex.fetch_bars_daily(market_id=47, code="IF2610"))
            stock = asyncio.run(ex.fetch_bars_daily(market_id=31, code="00700"))
        self.assertEqual(sent, [(4, 47, "IF2610"), (9, 31, "00700")])
        self.assertEqual(future.warnings, ("tdx_host=h:7727",))
        self.assertEqual(len(stock.rows), 1)

    def test_bars_project_onto_the_context_contract(self):
        binding = next(item for item in bindings_for("context.bars_daily", states=BINDING_STATES))
        projected = _normalise_rows(ex.parse_klines(IF_BARS, 4, 47, "IF2610"), binding)
        self.assertTrue(projected.canonical)
        self.assertEqual(projected.rows[1]["open_interest"], 50002)


if __name__ == "__main__":
    unittest.main()
