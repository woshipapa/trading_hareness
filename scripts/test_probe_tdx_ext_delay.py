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


class ExtDelayTests(unittest.TestCase):
    def setUp(self):
        blocked = mock.patch.object(socket.socket, "connect", side_effect=AssertionError("tests must not connect"))
        blocked.start()
        self.addCleanup(blocked.stop)

    def run_main(self, tencent, *extra):
        """main() against two fake hosts, the second 6 s late; the output file and the error that ended the run."""
        clock = Clock()
        shifts = {"1.1.1.1": 0, "2.2.2.2": 6}

        class Host:
            def __init__(self, host, port, timeout_seconds=5.0):
                self.shift = shifts[host]

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def quote(self, market, code):
                return {"price": float32(price_at(int(clock.now - START), self.shift) + (4000 if code == "IF2610" else 0))}

        error = None
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "delay.json"
            with (contextlib.redirect_stderr(io.StringIO()), mock.patch.object(MODULE, "time", clock),
                  mock.patch.object(MODULE.tdx_ex_market, "TdxExMarketClient", Host),
                  mock.patch.object(MODULE, "read_tencent", lambda key, timeout: tencent(clock, key))):
                try:
                    MODULE.main(["--hosts", "1.1.1.1:7727,2.2.2.2:7727", "--max-lag", "30", "--output", str(output), *extra])
                except ValueError as raised:
                    error = raised
            return json.loads(output.read_text(encoding="utf-8")), error

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
        payload, error = self.run_main(
            lambda clock, key: {"price": price_at(int(clock.now - START)), "server_time": "t"}, "--seconds", "120")
        self.assertIsNone(error)
        self.assertEqual(len(payload["readings"]), 120 * 5, "two hosts x two instruments, plus Tencent")
        self.assertEqual(payload["references"], {"27:HZ5017": "tencent:hkHSTECH", "47:IF2610": None})
        self.assertEqual([(row["instrument"], row["series"], row["behind"], row["lag_s"]) for row in payload["lags"]],
                         [("27:HZ5017", "1.1.1.1:7727", "tencent:hkHSTECH", 0),
                          ("27:HZ5017", "2.2.2.2:7727", "tencent:hkHSTECH", 6),
                          ("47:IF2610", "2.2.2.2:7727", "1.1.1.1:7727", 6)])

    def test_an_error_stops_the_run_but_the_readings_so_far_are_written(self):
        answers = []

        def tencent(clock, key):
            answers.append(key)
            if len(answers) == 6:
                raise ValueError("Tencent has no quote")
            return {"price": price_at(int(clock.now - START)), "server_time": "t"}

        payload, error = self.run_main(tencent, "--seconds", "120")
        self.assertEqual(str(error), "Tencent has no quote")
        self.assertEqual(len(payload["readings"]), 5 * 5 + 4, "five whole seconds, then the four host readings of the sixth")


if __name__ == "__main__":
    unittest.main()
