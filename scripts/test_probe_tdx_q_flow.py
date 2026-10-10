import contextlib
import importlib.util
import io
import json
import socket
import struct
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).with_name("probe-tdx-q-flow.py")
SPEC = importlib.util.spec_from_file_location("probe_tdx_q_flow", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

START = 1000.0


class Clock:
    """Time that moves only when the code sleeps."""

    def __init__(self):
        self.now = START

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def fake_mac(capped_from=None):
    """A MacClient whose n-th connection is the n-th sample. Each requested field of a 0x122b answer holds
    sample * 1000 + its bit; from sample ``capped_from`` on the host drops the fields 0x90 and above."""
    connections = []

    class Mac:
        def __init__(self):
            self.sample = len(connections)
            connections.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def exchange(self, request):
            if struct.unpack_from("<H", request, 10)[0] == 0x1218:
                return bytes(27) + json.dumps([["1", "2", "3", "4"], ["1", "2", "3", "4", "5", "6"]]).encode("gbk")
            asked = [bit for bit in range(160) if request[12 + bit // 8] >> (bit % 8) & 1]
            bits = [bit for bit in asked if capped_from is None or self.sample < capped_from or bit < 0x90]
            body = bytearray(MODULE.bitmap(tuple(bits))) + struct.pack("<IH", len(MODULE.SYMBOLS), len(MODULE.SYMBOLS))
            for symbol in MODULE.SYMBOLS:
                market, code = MODULE.market_code(symbol)
                body += struct.pack("<H22s44s", market, code.encode(), symbol.encode())
                body += struct.pack(f"<{len(bits)}f", *(self.sample * 1000.0 + bit for bit in bits))
            return bytes(body)

    return Mac


class FlowSamplingTests(unittest.TestCase):
    def setUp(self):
        blocked = mock.patch.object(socket.socket, "connect", side_effect=AssertionError("tests must not connect"))
        blocked.start()
        self.addCleanup(blocked.stop)

    def test_each_sample_holds_the_flow_fields_the_sampled_changes_and_the_reference_with_receive_times(self):
        clock = Clock()
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "flow.json"
            with (mock.patch.object(MODULE, "MacClient", fake_mac()), mock.patch.object(MODULE, "time", clock),
                  mock.patch.object(MODULE, "eastmoney", lambda: [{"f12": "000001", "f62": 1.5, "f184": 0.5}])):
                MODULE.sampling(2, 30.0, output)
            result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(clock.now - START, 30.0, "the second sample starts 30 s after the first")
        self.assertEqual(result["bits"]["0x90"], "change_at_1000")
        self.assertEqual(result["bits"]["0x96"], "change_at_1430")
        first, second = ({row["symbol"]: row for row in sample["mac_0x122b"]["rows"]} for sample in result["samples"])
        self.assertEqual([first["000001"][name] for name in ("main_net_amount", "change_at_1000", "change_at_1430")],
                         [56.0, 144.0, 150.0])
        self.assertEqual([second["000001"][name] for name in ("main_net_amount", "change_at_1000", "change_at_1430")],
                         [1056.0, 1144.0, 1150.0])
        self.assertEqual(len(first), len(MODULE.SYMBOLS))
        for sample in result["samples"]:
            self.assertEqual(sorted(sample), ["eastmoney", "flow_0x1218", "mac_0x122b"])
            for block in sample.values():
                datetime.fromisoformat(block["received_at"])
        self.assertEqual(result["samples"][0]["flow_0x1218"]["rows"]["000001.SZ"][0], ["1", "2", "3", "4"])
        self.assertEqual(result["samples"][0]["eastmoney"]["rows"], [{"f12": "000001", "f62": 1.5, "f184": 0.5}])
        self.assertEqual(sorted(result["reference"]["fields"]), ["f184", "f62"])

    def test_a_host_that_caps_the_fields_stops_the_run_and_the_samples_before_stay_in_the_file(self):
        clock = Clock()
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "flow.json"
            with (mock.patch.object(MODULE, "MacClient", fake_mac(capped_from=1)), mock.patch.object(MODULE, "time", clock),
                  mock.patch.object(MODULE, "eastmoney", lambda: []), self.assertRaises(MODULE.tdx_mac.TdxMacError)):
                MODULE.sampling(3, 30.0, output)
            self.assertEqual(len(json.loads(output.read_text(encoding="utf-8"))["samples"]), 1)

    def test_the_one_shot_fixture_still_decodes(self):
        with mock.patch.object(sys, "argv", ["probe-tdx-q-flow.py", "--fixture"]), \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            code = MODULE.main()
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(printed.getvalue())["fields"]), 1)


if __name__ == "__main__":
    unittest.main()
