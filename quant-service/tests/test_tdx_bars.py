import asyncio
import base64
import json
import struct
import unittest
from pathlib import Path
from unittest.mock import patch

from app.datasources.sources import tdx_bars, tdx_instruments, tdx_mac, tdx_protocol


FIXTURE = Path(__file__).parent / "fixtures" / "tdx_bars_20261009.json"


class IndexBarTests(unittest.TestCase):
    def test_index_fixture_has_constituent_breadth(self):
        fixture = json.loads(FIXTURE.read_text())
        for symbol, expected in (("999999.SH", (1341, 956)), ("399300.SZ", (179, 113))):
            body = base64.b64decode(fixture["index_daily"][symbol]["body_b64"])
            rows = tdx_instruments.parse_index_bars(body)
            self.assertEqual((rows[-1]["up_count"], rows[-1]["down_count"]), expected)

    def test_index_symbol_validation_precedes_network(self):
        async def fail(*_args, **_kwargs):
            raise AssertionError("network path reached")

        with patch.object(tdx_bars.tdx_protocol, "call", fail):
            with self.assertRaises(ValueError):
                asyncio.run(tdx_bars.fetch_index_daily(symbol="600519.SH", count=1))

    def test_index_adapter_returns_host_and_receive_time(self):
        fixture = json.loads(FIXTURE.read_text())
        body = base64.b64decode(fixture["index_daily"]["999999.SH"]["body_b64"])

        class Client:
            def _exchange(self, _request):
                return body

        async def call(operation, **_kwargs):
            return operation(Client()), "fixture-host:7709"

        with patch.object(tdx_bars.tdx_protocol, "call", call):
            evidence = asyncio.run(tdx_bars.fetch_index_daily(symbol="999999.SH", count=3))
        self.assertEqual(evidence.rows[-1]["up_count"], 1341)
        self.assertEqual(evidence.warnings, ("tdx_host=fixture-host:7709",))
        self.assertIsNotNone(evidence.available_at_min)
        self.assertEqual(evidence.available_at_min, evidence.available_at_max)


class LegacyBarTests(unittest.TestCase):
    def test_legacy_minute_matches_mac_fixture(self):
        fixture = json.loads(FIXTURE.read_text())
        for symbol in fixture["legacy_minute"]:
            legacy = tdx_protocol.parse_bars(
                8, base64.b64decode(fixture["legacy_minute"][symbol]["body_b64"])
            )
            mac = tdx_mac.parse_bars(base64.b64decode(fixture["mac_minute"][symbol]["body_b64"]))
            self.assertEqual(len(legacy), len(mac))
            for left, right in zip(legacy, mac):
                self.assertEqual(left["datetime"], f'{right["date"]} {right["seconds"] // 3600:02d}:{right["seconds"] // 60 % 60:02d}')
                self.assertEqual(left["volume"], right["volume"])
                self.assertEqual(left["amount"], right["amount"])
                for field in ("open", "high", "low", "close"):
                    self.assertAlmostEqual(left[field], right[field], delta=abs(right[field]) * 1e-4)

    def test_legacy_daily_volume_is_mac_shares_in_lots(self):
        fixture = json.loads(FIXTURE.read_text())
        for symbol in fixture["legacy_daily"]:
            legacy = tdx_protocol.parse_bars(9, base64.b64decode(fixture["legacy_daily"][symbol]["body_b64"]))
            mac = tdx_mac.parse_bars(base64.b64decode(fixture["mac_daily"][symbol]["body_b64"]))
            for left, right in zip(legacy, mac):
                self.assertLessEqual(abs(left["volume"] - right["volume"] / 100), 1)
                self.assertEqual(left["amount"], right["amount"])

    def test_paging_requests_800_rows_and_returns_newest_oldest_first(self):
        requests = []

        class Client:
            def _exchange(self, request):
                opcode = struct.unpack_from("<H", request, 10)[0]
                category = struct.unpack_from("<H", request, 20)[0]
                start, count = struct.unpack_from("<HH", request, 24)
                requests.append((opcode, category, start, count))
                return b"answer"

        def parse(_category, _body):
            start = requests[-1][2]
            return [{"datetime": f"2026-01-{index + 1:04d}", "open": 1, "high": 1, "low": 1,
                     "close": 1, "volume": 1, "amount": 1} for index in range(start, start + (800 if start == 0 else 200))]

        async def call(operation, **_kwargs):
            return operation(Client()), "fixture-host:7709"

        with patch.object(tdx_bars.tdx_protocol, "parse_bars", parse), patch.object(tdx_bars.tdx_protocol, "call", call):
            evidence = asyncio.run(tdx_bars.fetch_daily(symbol="600519.SH", count=1000))
        self.assertEqual(requests, [(0x052D, 9, 0, 800), (0x052D, 9, 800, 800)])
        self.assertEqual(len(evidence.rows), 1000)
        self.assertEqual(evidence.rows[0]["trade_date"], "2026-01-0001")
        self.assertEqual(evidence.rows[-1]["trade_date"], "2026-01-1000")

    def test_short_page_stops_paging(self):
        requests = []

        class Client:
            def _exchange(self, request):
                requests.append(struct.unpack_from("<H", request, 24)[0])
                return b"answer"

        with patch.object(tdx_bars.tdx_protocol, "parse_bars", return_value=[
            {"datetime": "2026-01-01", "open": 1, "high": 1, "low": 1,
             "close": 1, "volume": 2, "amount": 3}
        ]), patch.object(tdx_bars.tdx_protocol, "call", lambda operation, **_kwargs: _call(operation, Client(), "fixture-host:7709")):
            evidence = asyncio.run(tdx_bars.fetch_daily(symbol="600519.SH", count=1000))
        self.assertEqual(requests, [0])
        self.assertEqual(len(evidence.rows), 1)

    def test_legacy_symbol_and_count_validation_precedes_network(self):
        async def fail(*_args, **_kwargs):
            raise AssertionError("network path reached")

        with patch.object(tdx_bars.tdx_protocol, "call", fail):
            for symbol, count in (("bad", 1), ("600519.SH", 0)):
                with self.assertRaises(ValueError):
                    asyncio.run(tdx_bars.fetch_minute(symbol=symbol, count=count))

    def test_legacy_minute_evidence_has_host_and_receive_time(self):
        class Client:
            def _exchange(self, _request):
                return b"answer"

        async def call(operation, **_kwargs):
            return operation(Client()), "fixture-host:7709"

        with patch.object(tdx_bars.tdx_protocol, "parse_bars", return_value=[
            {"datetime": "2026-10-09 15:00", "open": 1, "high": 1, "low": 1,
             "close": 1, "volume": 2, "amount": 3}
        ]), patch.object(tdx_bars.tdx_protocol, "call", call):
            evidence = asyncio.run(tdx_bars.fetch_minute(symbol="600519.SH", count=1))
        self.assertEqual(evidence.warnings, ("tdx_host=fixture-host:7709",))
        self.assertEqual(evidence.available_at_min, evidence.available_at_max)


async def _call(operation, client, host):
    return operation(client), host
