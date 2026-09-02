import os
import unittest
from unittest.mock import patch

from app.main import intraday_longhu_max_symbols


class LonghuIntradayLimitTests(unittest.TestCase):
    def test_default_does_not_truncate_watchlist_at_local_24_or_60(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(intraday_longhu_max_symbols(), 300)

    def test_explicit_operational_limit_is_respected_but_capped_by_gateway(self):
        with patch.dict(os.environ, {"QUANT_LONGHU_INTRADAY_MAX_SYMBOLS": "100"}, clear=True):
            self.assertEqual(intraday_longhu_max_symbols(), 100)
        with patch.dict(os.environ, {"QUANT_LONGHU_INTRADAY_MAX_SYMBOLS": "1000"}, clear=True):
            self.assertEqual(intraday_longhu_max_symbols(), 300)


if __name__ == "__main__":
    unittest.main()
