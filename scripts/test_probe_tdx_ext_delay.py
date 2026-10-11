import collections
import contextlib
import importlib.util
import io
import json
import socket
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).with_name("probe-tdx-ext-delay.py")
SPEC = importlib.util.spec_from_file_location("probe_tdx_ext_delay", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

START = 5000.0
#: The price changes of a feed: (second, price), each held 1 to 4 s, five values that do not settle into a pattern.
CHANGES = []
SECOND = 0
for index in range(90):
    CHANGES.append((SECOND, round(100.0 + 0.2 * ((index * index * 3 + index) % 5), 1)))
    SECOND += 1 + (index * 7) % 4


def price_at(second, shift=0):
    """The price a feed that runs ``shift`` seconds late shows at ``second``."""
    shown = [price for start, price in CHANGES if start + shift <= second]
    return shown[-1] if shown else CHANGES[0][1]


def float32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def feed(source, instrument, shift=0, wire=False, seconds=150):
    """One reading per second of a feed; ``wire`` sends the price as the float32 a TDX host sends."""
    return [{"source": source, "instrument": instrument, "at": float(second),
             "price": float32(price_at(second, shift)) if wire else price_at(second, shift)} for second in range(seconds)]


class Clock:
    """Time that moves only when the code sleeps."""

    def __init__(self):
        self.now = START

    def monotonic(self):
        return self.now

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def good_tencent(clock, key):
    return {"price": price_at(int(clock.now - START)), "server_time": "t"}


class ExtDelayTests(unittest.TestCase):
    def setUp(self):
        blocked = mock.patch.object(socket.socket, "connect", side_effect=AssertionError("tests must not connect"))
        blocked.start()
        self.addCleanup(blocked.stop)

    def run_main(self, tencent, *extra, host_fails=None):
        """main() against two fake hosts, the second 6 s late. ``host_fails`` maps (host, second) to the exception
        that host raises then. The output file, the exit status, the exception that ended the run (a KeyError is
        the one a test raises) and the connections each host saw."""
        clock, shifts, connections, status, error = Clock(), {"1.1.1.1": 0, "2.2.2.2": 6}, collections.Counter(), None, None
        host_fails = host_fails or {}

        class Host:
            def __init__(self, host, port, timeout_seconds=5.0):
                self.host, self.shift = host, shifts[host]
                connections[host] += 1

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def quote(self, market, code):
                second = int(clock.now - START)
                if (self.host, second) in host_fails:
                    raise host_fails[self.host, second]
                return {"price": float32(price_at(second, self.shift) + (4000 if code == "IF2610" else 0))}

        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "delay.json"
            with (contextlib.redirect_stderr(io.StringIO()), mock.patch.object(MODULE, "time", clock),
                  mock.patch.object(MODULE.tdx_ex_market, "TdxExMarketClient", Host),
                  mock.patch.object(MODULE, "read_tencent", lambda key, timeout: tencent(clock, key))):
                try:
                    status = MODULE.main(["--hosts", "1.1.1.1:7727,2.2.2.2:7727", "--max-lag", "30", "--output", str(output),
                                          *extra])
                except KeyError as raised:
                    error = raised
            return json.loads(output.read_text(encoding="utf-8")), status, error, connections

    def test_lag_is_the_shift_at_which_price_changes_recur(self):
        readings = feed("tencent", "27:HZ5017") + feed("host", "27:HZ5017", shift=7, wire=True)
        reference = MODULE.price_series(readings, "tencent", "27:HZ5017")
        late = MODULE.price_series(readings, "host", "27:HZ5017")
        found = MODULE.lag(late, reference, max_lag=30)
        self.assertGreater(found["matched_changes"], 20)
        self.assertEqual((found["lag_s"], found["matched_changes"]), (7, len(MODULE.price_changes(late))))
        self.assertEqual(MODULE.lag(reference, late, max_lag=30)["lag_s"], -7, "the reference is 7 s ahead of the host")

    def test_changes_match_only_with_the_same_price(self):
        def ramp(shift):      # both feeds change every 2 s: only the prices tell a 6 s shift from none
            return [(float(second), round(100 + 0.2 * (max(second - shift, 0) // 2), 1)) for second in range(60)]

        self.assertEqual(MODULE.lag(ramp(6), ramp(0), max_lag=10)["lag_s"], 6)

    def test_every_host_and_tencent_are_read_each_second_and_the_lags_reported(self):
        payload, status, error, connections = self.run_main(good_tencent, "--seconds", "120")
        self.assertEqual((status, error, payload["errors"]), (0, None, 0))
        self.assertNotIn("stopped", payload)
        self.assertEqual(len(payload["readings"]), 120 * 5, "two hosts x two instruments, plus Tencent")
        self.assertEqual(payload["references"], {"27:HZ5017": "tencent:hkHSTECH", "47:IF2610": None})
        self.assertEqual([(row["instrument"], row["series"], row["behind"], row["lag_s"]) for row in payload["lags"]],
                         [("27:HZ5017", "1.1.1.1:7727", "tencent:hkHSTECH", 0),
                          ("27:HZ5017", "2.2.2.2:7727", "tencent:hkHSTECH", 6),
                          ("47:IF2610", "2.2.2.2:7727", "1.1.1.1:7727", 6)])
        self.assertEqual(connections, {"1.1.1.1": 1, "2.2.2.2": 1})

    def test_a_failed_read_is_an_entry_of_its_second_and_the_run_goes_on(self):
        def tencent(clock, key):
            if int(clock.now - START) == 3:
                raise OSError("t" * 500)
            return good_tencent(clock, key)

        payload, status, error, connections = self.run_main(
            tencent, "--seconds", "120", host_fails={("2.2.2.2", 5): ConnectionResetError("reset")})
        self.assertEqual((status, error, payload["errors"]), (0, None, 2))
        self.assertNotIn("stopped", payload)
        failed = [reading for reading in payload["readings"] if "error" in reading]
        self.assertEqual([(entry["source"], entry["error"], entry["message"], entry["at"]) for entry in failed],
                         [("tencent:hkHSTECH", "OSError", "t" * 200, START + 3),
                          ("2.2.2.2:7727", "ConnectionResetError", "reset", START + 5)])
        self.assertEqual(len(payload["readings"]), 120 * 5 - 1, "the failed host gave one entry for its two readings")
        self.assertEqual(connections, {"1.1.1.1": 1, "2.2.2.2": 2}, "the host that failed is connected again")
        self.assertEqual([row["lag_s"] for row in payload["lags"]], [0, 6, 6], "the series are still compared")

    def test_failed_seconds_in_a_row_stop_the_run_and_say_so(self):
        def tencent(clock, key):
            if int(clock.now - START) >= 2:
                raise OSError("down")
            return good_tencent(clock, key)

        for extra, in_a_row in ((("--max-consecutive-errors", "3"), 3), ((), 5)):
            payload, status, _, _ = self.run_main(tencent, "--seconds", "120", *extra)
            self.assertEqual(payload["stopped"], f"{in_a_row} seconds in a row with a failed read")
            self.assertEqual((status, payload["errors"], len(payload["readings"])), (1, in_a_row, (2 + in_a_row) * 5))

    def test_failures_that_are_not_in_a_row_do_not_stop_the_run(self):
        def tencent(clock, key):
            if int(clock.now - START) in (2, 3, 4, 5, 7, 8, 9, 10):
                raise OSError("down")
            return good_tencent(clock, key)

        payload, status, _, _ = self.run_main(tencent, "--seconds", "20")
        self.assertEqual((status, payload["errors"], len(payload["readings"])), (0, 8, 20 * 5))
        self.assertNotIn("stopped", payload)

    def test_an_exception_of_another_kind_ends_the_run_and_the_readings_so_far_are_written(self):
        answers = []

        def tencent(clock, key):
            answers.append(key)
            if len(answers) == 6:
                raise KeyError("data")
            return good_tencent(clock, key)

        payload, status, error, _ = self.run_main(tencent, "--seconds", "120")
        self.assertEqual((status, str(error)), (None, "'data'"))
        self.assertEqual(len(payload["readings"]), 5 * 5 + 4, "five whole seconds, then the four host readings of the sixth")


if __name__ == "__main__":
    unittest.main()
