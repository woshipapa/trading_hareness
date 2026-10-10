import contextlib
import importlib.util
import io
import json
import socket
import struct
import subprocess
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
ALL_BLOCKS = ["eastmoney", "flow_0x1218", "mac_0x122b"]


class Clock:
    """Time that moves only when the code sleeps."""

    def __init__(self):
        self.now = START

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def fake_mac(capped_from=None, fails=None):
    """A MacClient whose n-th connection is the n-th sample. Each requested field of a 0x122b answer holds
    sample * 1000 + its bit; from sample ``capped_from`` on the host drops the fields 0x90 and above, and the
    0x122b exchange of a sample in ``fails`` raises the exception it maps to."""
    connections = []
    fails = fails or {}

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
            if self.sample in fails:
                raise fails[self.sample]
            asked = [bit for bit in range(160) if request[12 + bit // 8] >> (bit % 8) & 1]
            bits = [bit for bit in asked if capped_from is None or self.sample < capped_from or bit < 0x90]
            body = bytearray(MODULE.bitmap(tuple(bits))) + struct.pack("<IH", len(MODULE.SYMBOLS), len(MODULE.SYMBOLS))
            for symbol in MODULE.SYMBOLS:
                market, code = MODULE.market_code(symbol)
                body += struct.pack("<H22s44s", market, code.encode(), symbol.encode())
                body += struct.pack(f"<{len(bits)}f", *(self.sample * 1000.0 + bit for bit in bits))
            return bytes(body)

    return Mac


def references(rows=None):
    return lambda: [{"f12": "000001", "f62": 1.5, "f184": 0.5}] if rows is None else rows


class FlowSamplingTests(unittest.TestCase):
    def setUp(self):
        blocked = mock.patch.object(socket.socket, "connect", side_effect=AssertionError("tests must not connect"))
        blocked.start()
        self.addCleanup(blocked.stop)

    def run_sampling(self, count, mac, eastmoney=references()):
        """sampling() on fakes with the default limit: the output file, whether the run stopped, the clock and the
        KeyError that ended the run, if any (the one a test raises)."""
        clock, stopped, error = Clock(), None, None
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "flow.json"
            with (contextlib.redirect_stderr(io.StringIO()), mock.patch.object(MODULE, "MacClient", mac),
                  mock.patch.object(MODULE, "time", clock), mock.patch.object(MODULE, "eastmoney", eastmoney)):
                try:
                    stopped = MODULE.sampling(count, 30.0, 5, output)
                except KeyError as raised:
                    error = raised
            return json.loads(output.read_text(encoding="utf-8")), stopped, clock, error

    def test_each_sample_holds_the_flow_fields_the_sampled_changes_and_the_reference_with_receive_times(self):
        result, stopped, clock, error = self.run_sampling(2, fake_mac())
        self.assertEqual((stopped, error, result["errors"]), (False, None, 0))
        self.assertNotIn("stopped", result)
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
            self.assertEqual(sorted(sample), ALL_BLOCKS)
            for block in sample.values():
                datetime.fromisoformat(block["received_at"])
        self.assertEqual(result["samples"][0]["flow_0x1218"]["rows"]["000001.SZ"][0], ["1", "2", "3", "4"])
        self.assertEqual(result["samples"][0]["eastmoney"]["rows"], [{"f12": "000001", "f62": 1.5, "f184": 0.5}])
        self.assertEqual(sorted(result["reference"]["fields"]), ["f184", "f62"])

    def test_a_failed_sample_is_an_entry_that_keeps_the_blocks_read_before_it_and_the_run_goes_on(self):
        calls = []

        def eastmoney():
            calls.append(1)
            if len(calls) == 3:      # the reference of sample 7: the samples 1-4 and 6 failed before it
                raise subprocess.CalledProcessError(22, ["curl"])
            return [{"f12": "000001"}]

        resets = {number: ConnectionResetError("r" * 300) for number in (1, 2, 3, 4, 6)}
        result, stopped, _, error = self.run_sampling(9, fake_mac(fails=resets), eastmoney)
        first, reset = result["samples"][0], result["samples"][1]
        self.assertEqual((stopped, error, result["errors"]), (False, None, 6), "six failed, never five in a row")
        self.assertNotIn("stopped", result)
        self.assertEqual([sorted(sample) == ALL_BLOCKS for sample in result["samples"]],
                         [True, False, False, False, False, True, False, False, True])
        self.assertEqual(sorted(reset), ["at", "error", "message"], "nothing was read before the exchange failed")
        self.assertEqual((reset["error"], reset["message"]), ("ConnectionResetError", "r" * 200))
        datetime.fromisoformat(reset["at"])
        partial = result["samples"][7]
        self.assertEqual(sorted(partial), ["at", "error", "flow_0x1218", "mac_0x122b", "message"])
        self.assertEqual(partial["error"], "CalledProcessError")
        self.assertEqual(sorted(first), ALL_BLOCKS)

    def test_an_eastmoney_answer_without_data_is_a_value_error_and_a_good_one_is_read_in_batches(self):
        answers = iter(['{"data": null}', '{"data": {"diff": [{"f12": "000001"}]}}', '{"data": {"diff": [{"f12": "600519"}]}}'])
        with mock.patch.object(MODULE.subprocess, "run", lambda *_args, **_kwargs: mock.Mock(stdout=next(answers))):
            with self.assertRaises(ValueError):
                MODULE.eastmoney()
            self.assertEqual(MODULE.eastmoney(), [{"f12": "000001"}, {"f12": "600519"}], "eight symbols, two requests")

    def test_failed_samples_in_a_row_stop_the_run_and_say_so(self):
        for extra, in_a_row in (((), 5), (("--max-consecutive-errors", "2"), 2)):
            with tempfile.TemporaryDirectory() as root:
                output = Path(root) / "flow.json"
                argv = ["probe-tdx-q-flow.py", "--samples", "20", "--interval", "30", "--output", str(output), *extra]
                with (mock.patch.object(sys, "argv", argv), contextlib.redirect_stderr(io.StringIO()),
                      mock.patch.object(MODULE, "MacClient", fake_mac(capped_from=1)),
                      mock.patch.object(MODULE, "time", Clock()), mock.patch.object(MODULE, "eastmoney", references())):
                    code = MODULE.main()
                result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(code, 1)
            self.assertEqual(result["stopped"], f"{in_a_row} samples in a row failed")
            self.assertEqual((result["errors"], len(result["samples"])), (in_a_row, 1 + in_a_row))
            self.assertEqual({sample["error"] for sample in result["samples"][1:]}, {"TdxMacError"})

    def test_an_exception_of_another_kind_ends_the_run_and_the_samples_so_far_stay_in_the_file(self):
        result, stopped, _, error = self.run_sampling(3, fake_mac(fails={1: KeyError("data")}))
        self.assertEqual((stopped, str(error)), (None, "'data'"))
        self.assertEqual(len(result["samples"]), 1)

    def test_the_one_shot_fixture_still_decodes(self):
        with mock.patch.object(sys, "argv", ["probe-tdx-q-flow.py", "--fixture"]), \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            code = MODULE.main()
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(printed.getvalue())["fields"]), 1)


if __name__ == "__main__":
    unittest.main()
