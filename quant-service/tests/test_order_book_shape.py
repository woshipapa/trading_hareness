import unittest

from app.intraday_price_priority import order_book_from_row

# Verbatim shapes the licensed source returned on 2026-09-18.
BATCHED = {"ts_code": "002156.SZ", "name": "通富微电", "price": 61.65, "pre_close": 58.31,
           "bids": [{"price": 61.65, "size": 771.0}, {"price": 61.64, "size": 6.0}],
           "asks": [{"price": 61.66, "size": 73.0}, {"price": 61.67, "size": 197.0}]}
NESTED = {"ts_code": "002156.SZ", "order_book": {
    "bids": [{"price": 61.65, "size": 771.0}], "asks": [{"price": 61.66, "size": 73.0}],
    "book_side": "two_sided", "one_sided_book": False, "seal_volume_lot": None,
    "source": "longhuvip:GetStockPanKou"}}


class OrderBookShapeTests(unittest.TestCase):
    """The batched watch call and the single quote return different shapes."""

    def test_the_batched_row_yields_a_book(self):
        # Reading only the nested key discarded every batched row, so the loop
        # found nothing licensed and ran the session on the Tencent fallback
        # while the licensed levels sat unread in the response.
        book = order_book_from_row(BATCHED)
        self.assertIsNotNone(book)
        self.assertEqual(len(book["bids"]), 2)
        self.assertEqual(book["book_side"], "two_sided")
        self.assertEqual(book["source"], "longhuvip:GetStockPanKou")

    def test_the_nested_row_is_returned_as_is(self):
        self.assertEqual(order_book_from_row(NESTED)["book_side"], "two_sided")

    def test_both_shapes_agree_on_the_same_levels(self):
        # A consumer must not be able to tell which call produced the book.
        batched, nested = order_book_from_row(BATCHED), order_book_from_row(NESTED)
        self.assertEqual(batched["bids"][0], nested["bids"][0])
        self.assertEqual(batched["book_side"], nested["book_side"])

    def test_a_sealed_board_reports_its_side_and_seal_size(self):
        book = order_book_from_row({"bids": [{"price": 10.0, "size": 5000.0}], "asks": []})
        self.assertEqual(book["book_side"], "bid_only")
        self.assertTrue(book["one_sided_book"])
        self.assertEqual(book["seal_volume_lot"], 5000.0)

    def test_a_limit_down_board_reports_the_ask_side(self):
        book = order_book_from_row({"bids": [], "asks": [{"price": 9.0, "size": 8000.0}]})
        self.assertEqual(book["book_side"], "ask_only")
        self.assertEqual(book["seal_volume_lot"], 8000.0)

    def test_a_row_with_no_levels_yields_nothing(self):
        self.assertIsNone(order_book_from_row({"bids": [], "asks": []}))
        self.assertIsNone(order_book_from_row({}))

    def test_an_empty_nested_book_falls_through_to_the_top_level(self):
        row = {"order_book": {"bids": [], "asks": []}, "bids": [{"price": 1.0, "size": 2.0}], "asks": []}
        self.assertEqual(order_book_from_row(row)["book_side"], "bid_only")

    def test_malformed_levels_are_ignored_rather_than_stored(self):
        book = order_book_from_row({"bids": ["nonsense", {"price": 1.0, "size": 2.0}], "asks": []})
        self.assertEqual(len(book["bids"]), 1)


if __name__ == "__main__":
    unittest.main()
