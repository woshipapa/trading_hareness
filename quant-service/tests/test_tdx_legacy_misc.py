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
        rows = legacy._all_a_snapshot(client)
        self.assertEqual(len(rows), 81)
        self.assertEqual(client.requests, [(0, 80), (80, 80)])

    def test_index_overview_decodes_breadth_counts(self):
        body = struct.pack("<IB6sH", 1, 1, b"000001", 7)
        body += b"".join(enc(value) for value in (1000, 10, 20, 30, 5, 930, 0, 123, 4))
        body += struct.pack("<f", 12.5)
        body += b"".join(enc(value) for value in (0, 0, 99, 0, 0, 0, 12, 8))
        body += b"".join(enc(0) for _ in range(10))
        body += b"".join(enc(value) for value in (25, 3, 40))
        parsed = legacy.parse_index_info(body)
        self.assertEqual((parsed["up_count"], parsed["down_count"]), (12, 8))
        self.assertEqual(parsed["orders"][0]["price"], 0.25)

    def test_index_momentum_is_delta_encoded(self):
        body = struct.pack("<H", 3) + enc(4) + enc(-1) + enc(7)
        self.assertEqual(legacy.parse_index_momentum(body), [4, 3, 10])

    def test_removed_commands_and_client_subclass_are_absent(self):
        for name in ("KMSG_QUOTESENCRYPT", "KMSG_SECURITYFEATURE452", "KMSG_TODOB", "KMSG_TODOFDE",
                     "KMSG_CHARTSAMPLING", "KMSG_SECURITYBARS_OFFSET", "KMSG_TRANSACTIONDATA_TRANS",
                     "build_encrypted_quotes_request", "build_security_feature452_request", "build_todob_request",
                     "build_todofde_request", "build_chart_sampling_request", "build_security_bars_offset_request",
                     "LegacyMiscClient"):
            self.assertFalse(hasattr(legacy, name), name)

    def test_st_filter_is_not_a_builder_argument(self):
        self.assertNotIn("filter", inspect.signature(legacy.build_quotes_list_request).parameters)

    def test_truncated_responses_raise_protocol_error(self):
        with self.assertRaises(tdx_protocol.TdxProtocolError):
            legacy.parse_quotes_list(b"\x00")
        with self.assertRaises(tdx_protocol.TdxProtocolError):
            legacy.parse_index_info(b"\x00" * 16)

    def test_snapshot_adapter_records_host_and_is_research_only(self):
        with mock.patch.object(tdx_protocol, "call", new=mock.AsyncMock(return_value=([{"price": 1}], "host:7709/login_one"))):
            result = asyncio.run(legacy.fetch_all_a_snapshot())
        self.assertEqual(result.coverage, 1.0)
        self.assertEqual(result.warnings, ("tdx_host=host:7709/login_one",))


if __name__ == "__main__":
    unittest.main()
