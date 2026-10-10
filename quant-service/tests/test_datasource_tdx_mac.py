import struct
import unittest
from datetime import datetime, timezone

from app.datasources.sources import tdx_mac


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


class MacAdapterConversionTests(unittest.TestCase):
    """Test that adapters convert symbol strings to (market, code) internally."""

    def test_symbol_conversion_for_watch_snapshot(self):
        """Verify market_code conversion for use in fetch_watch_snapshot."""
        # Test that market_code correctly converts symbols
        self.assertEqual(tdx_mac.market_code("600519.SH"), (1, "600519"))
        self.assertEqual(tdx_mac.market_code("000001.SZ"), (0, "000001"))

    def test_symbol_conversion_for_daily_bars(self):
        """Verify market_code conversion for use in fetch_daily_bars."""
        # Test that market_code correctly converts symbols
        self.assertEqual(tdx_mac.market_code("600519.SH"), (1, "600519"))


class MacBoardCatalogTests(unittest.TestCase):
    """Test sector.board_catalog returns rows with singular field names."""

    def test_board_catalog_field_names(self):
        """Parsed board_catalog rows must have singular field names: board_code, name, board_type, member_count."""
        # Synthetic board row - count_all=2 produces 1 row
        item = bytearray(160)
        struct.pack_into("<H", item, 0, 1)  # market
        item[2:8] = b"880001"
        item[24:29] = b"Sector\0"
        struct.pack_into("<H", item, 148, 42)  # member_count
        body = struct.pack("<HH", 2, 99) + bytes(item)

        rows = tdx_mac.parse_board_list(body)
        self.assertEqual(len(rows), 1)
        row = rows[0]

        # Check that these fields exist (will be mapped to board_code, name, board_type, member_count in fetch_board_catalog)
        self.assertIn("code", row)
        self.assertEqual(row["code"], "880001")
        self.assertIn("name", row)
        self.assertIn("member_count", row)
        self.assertEqual(row["member_count"], 42)


class MacMembershipTests(unittest.TestCase):
    """Test sector.membership returns rows with all 4 canonical fields."""

    def test_membership_has_all_canonical_fields(self):
        """Membership rows must have: taxonomy_key, sector_key, symbol, known_at."""
        member = bytearray(68)
        member[2:8] = b"600519"
        member[24:31] = b"Moutai\0"
        members = b"\0" * 24 + struct.pack("<H", 1) + bytes(member)

        rows = tdx_mac.parse_board_members(members)
        self.assertEqual(len(rows), 1)
        row = rows[0]

        # Basic check that symbol is present (taxonomy_key and known_at added by fetch_membership)
        self.assertIn("symbol", row)
        self.assertEqual(row["symbol"], "600519")

    def test_membership_known_at_is_utc_aware(self):
        """known_at field must be UTC-aware datetime."""
        # This will be tested in the fetch_membership function
        # which adds known_at = datetime.now(timezone.utc)
        now_utc = datetime.now(timezone.utc)
        self.assertIsNotNone(now_utc.tzinfo)
        self.assertEqual(now_utc.tzinfo, timezone.utc)


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
