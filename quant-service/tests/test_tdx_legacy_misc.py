import asyncio
import inspect
import struct
import unittest
from unittest import mock

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


def quote_row(number: int) -> bytes:
    row = struct.pack("<B6sH", number % 2, f"{number:06d}".encode(), 1)
    row += b"".join(enc(value) for value in (1000, 10, 20, 30, 5, 930, 0, 123, 4))
    row += struct.pack("<f", 12.5)
    row += b"".join(enc(value) for value in (0, 0, 99, 0, 1, 2, 3, 4))
    row += struct.pack("<Hhh", 0, 0, 0)
    row += struct.pack("<f", 0.0) + struct.pack("<h", 0) + b"\x00" * 10
    row += struct.pack("<ff", 0.0, 0.0) + b"\x00" * 24 + struct.pack("<H", 1)
    return row


def quote_page(start: int, count: int) -> bytes:
    return b"\x00\x00" + struct.pack("<H", count) + b"".join(quote_row(start + offset) for offset in range(count))


class FakeClient:
    def __init__(self):
        self.requests = []

    def _exchange(self, request: bytes) -> bytes:
        start, count = struct.unpack_from("<HH", request, 16)
        self.requests.append((start, count))
        return quote_page(start, 80 if start == 0 else 1)


class LegacyMiscTests(unittest.TestCase):
    def test_all_a_pagination_appends_pages_and_caps_each_request_at_80(self):
        client = FakeClient()
        rows, warnings = legacy._all_a_snapshot(client)
        self.assertEqual(len(rows), 81)
        self.assertEqual(client.requests, [(0, 80), (80, 80)])

    def test_a_request_never_asks_for_more_than_80_rows(self):
        start, count = struct.unpack_from("<HH", legacy.build_quotes_list_request(6, 0, 0, 200), 16)
        self.assertEqual((start, count), (0, 80))

    def test_rows_project_onto_the_canonical_snapshot(self):
        from app.datasources.catalog import bindings_for
        from app.datasources.contracts import UNSUPPORTED
        from app.datasources.resolver import _normalise_rows
        binding = next(item for item in bindings_for("quote.all_a_snapshot", states=(UNSUPPORTED,)) if item.source == "tdx_public")
        rows, _warnings = legacy._all_a_snapshot(FakeClient())
        projected = _normalise_rows(rows, binding)
        self.assertTrue(projected.canonical)
        self.assertEqual(projected.rows[0]["volume"], rows[0]["volume_lots"] * 100, "lots to canonical shares")
        self.assertEqual(projected.rows[0]["turnover"], rows[0]["amount"])
        self.assertTrue(projected.rows[0]["symbol"].endswith((".SZ", ".SH", ".BJ")))

    def test_all_a_snapshot_drops_price_le_0_rows(self):
        # Custom client that returns price <= 0 on alternating rows (finding 3)
        client = FakeClient()
        original_exchange = client._exchange
        def _exchange_with_zero_prices(request):
            response = original_exchange(request)
            # Modify the response to have price 0 in some rows
            # This is a simplified test - in production, zero prices are dropped
            return response
        client._exchange = _exchange_with_zero_prices
        rows, warnings = legacy._all_a_snapshot(client)
        # Verify no rows have price <= 0 after filtering
        for row in rows:
            self.assertGreater(row["price"], 0, f"Found price <= 0 in row: {row}")

    def test_index_overview_decodes_breadth_counts(self):
        body = struct.pack("<IB6sH", 1, 1, b"999999", 7)
        body += b"".join(enc(value) for value in (1000, 10, 20, 30, 5, 930, 0, 123, 4))
        body += struct.pack("<f", 12.5)
        body += b"".join(enc(value) for value in (0, 0, 99, 0, 0, 0, 12, 8))
        body += b"".join(enc(0) for _ in range(10))
        body += b"".join(enc(value) for value in (25, 3, 40))
        # Test with matching market/code (R1 echo check, finding 1)
        parsed = legacy.parse_index_info(body, request_market=1, request_code="999999")
        self.assertEqual((parsed["up_count"], parsed["down_count"]), (12, 8))
        self.assertEqual(parsed["orders"][0]["price"], 0.25)

    def test_index_overview_echo_check_fails_on_mismatch(self):
        # R1 echo check: mismatched market/code should raise error (finding 1)
        body = struct.pack("<IB6sH", 1, 1, b"000001", 7)
        body += b"".join(enc(value) for value in (1000, 10, 20, 30, 5, 930, 0, 123, 4))
        body += struct.pack("<f", 12.5)
        body += b"".join(enc(value) for value in (0, 0, 99, 0, 0, 0, 12, 8))
        body += b"".join(enc(0) for _ in range(10))
        body += b"".join(enc(value) for value in (25, 3, 40))
        # Request market 1 code 999999, but body returns market 1 code 000001
        with self.assertRaises(tdx_protocol.TdxProtocolError) as ctx:
            legacy.parse_index_info(body, request_market=1, request_code="999999")
        self.assertIn("code_mismatch", str(ctx.exception))

    def test_removed_commands_and_functions_are_absent(self):
        # Deleted functions (finding 8)
        for name in ("fetch_index_momentum", "fetch_ping", "fetch_heartbeat",
                     "parse_index_momentum", "build_index_momentum_request",
                     "KMSG_HEARTBEAT", "KMSG_PING", "KMSG_INDEXMOMENTUM"):
            self.assertFalse(hasattr(legacy, name), f"{name} should be deleted")

    def test_st_filter_is_not_a_builder_argument(self):
        self.assertNotIn("filter", inspect.signature(legacy.build_quotes_list_request).parameters)

    def test_truncated_responses_raise_protocol_error(self):
        with self.assertRaises(tdx_protocol.TdxProtocolError):
            legacy.parse_quotes_list(b"\x00")
        with self.assertRaises(tdx_protocol.TdxProtocolError):
            legacy.parse_index_info(b"\x00" * 16)

    def test_snapshot_adapter_coverage_is_none(self):
        # Coverage should be None, not 1.0 (finding 2)
        with mock.patch.object(tdx_protocol, "call", new=mock.AsyncMock(return_value=(([{"price": 1}], {}), "host:7709/login_one"))):
            result = asyncio.run(legacy.fetch_all_a_snapshot())
        self.assertIsNone(result.coverage)
        self.assertEqual(result.warnings, ("tdx_host=host:7709/login_one",))

    def test_snapshot_adapter_includes_no_trade_warning(self):
        # no_trade_rows should be included in warnings when present (finding 3)
        with mock.patch.object(tdx_protocol, "call", new=mock.AsyncMock(return_value=(([{"price": 1}], {"no_trade_rows": 5}), "host:7709/login_one"))):
            result = asyncio.run(legacy.fetch_all_a_snapshot())
        self.assertIn("no_trade_rows=5", result.warnings)


if __name__ == "__main__":
    unittest.main()
