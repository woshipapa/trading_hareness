import asyncio
import base64
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from app.datasources.sources import tdx_bars, tdx_instruments


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
