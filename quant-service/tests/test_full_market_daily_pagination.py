import unittest

from app.full_market_daily_sync import DAILY_MAX_PAGES, DAILY_MAX_ROWS, DAILY_PAGE_SIZE


class FullMarketDailyPaginationTests(unittest.TestCase):
    """A session's all-A bars are ~5,400 rows and are refused in one response."""

    def test_the_page_size_stays_within_the_rest_adapter_cap(self):
        # The REST adapter clamps limit to 3000; a larger page would be
        # silently reduced and the offsets would then disagree with the pages.
        self.assertLessEqual(DAILY_PAGE_SIZE, 3000)

    def test_the_page_budget_can_reach_the_row_budget(self):
        self.assertGreaterEqual(DAILY_MAX_PAGES * DAILY_PAGE_SIZE, DAILY_MAX_ROWS)

    def test_the_row_budget_leaves_room_above_the_current_market(self):
        # ~5,600 listed names today; a budget at that number would start
        # truncating the moment the market grows.
        self.assertGreaterEqual(DAILY_MAX_ROWS, 8000)


if __name__ == "__main__":
    unittest.main()
