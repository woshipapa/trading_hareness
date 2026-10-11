import contextlib
import importlib.util
import io
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).with_name("probe-tdx-cadence.py")
SPEC = importlib.util.spec_from_file_location("probe_tdx_cadence", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Clock:
    """Time that moves only when the code sleeps or a fake host takes time."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Link:
    """A fake connection that writes its opens and closes into a shared ledger."""

    def __init__(self, ledger):
        self.ledger = ledger

    def __enter__(self):
        self.ledger.append("open")
        return self

    def __exit__(self, *_exc):
        self.ledger.append("close")


class CadenceTests(unittest.TestCase):
    def setUp(self):
        blocked = mock.patch.object(socket.socket, "connect", side_effect=AssertionError("tests must not connect"))
        blocked.start()
        self.addCleanup(blocked.stop)

    def test_requests_start_one_interval_apart(self):
        with mock.patch.object(MODULE, "time", Clock()):
            starts = [round(start, 3) for start in MODULE.due_times(5, 2)]
        self.assertEqual(starts, [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8])

    def test_a_late_request_is_not_made_up_for_with_a_burst(self):
        clock = Clock()
        starts = []
        with mock.patch.object(MODULE, "time", clock):
            for start in MODULE.due_times(5, 2):
                starts.append(round(start, 3))
                clock.now += 0.55 if len(starts) == 1 else 0.01      # the first answer takes almost three intervals
        self.assertEqual(starts, [0.0, 0.55, 0.75, 0.95, 1.15, 1.35, 1.55, 1.75])

    def test_summary_counts_failures_and_takes_percentiles_of_the_answers(self):
        records = [{"at": index, "ms": float(index + 1), "digest": "a" if index < 10 else "b"} for index in range(21)]
        records[0]["connect_ms"] = 30.0
        records += [{"at": 21, "connect_ms": 5000.0, "error": "TimeoutError"},
                    {"at": 22, "ms": 5000.0, "error": "TdxProtocolError"}]
        self.assertEqual(MODULE.summarize(records, 23), {
            "requests": 23, "failures": 2, "failure_rate": 0.087,
            "errors": {"TimeoutError": 1, "TdxProtocolError": 1}, "achieved_rate": 1.0,
            "p50_ms": 11.0, "p95_ms": 20.0, "connect_p50_ms": 30.0, "connect_p95_ms": 30.0,
            "digest_changes": 1, "distinct_digests": 2})

    def test_one_connection_serves_a_kept_open_run_until_it_fails_and_every_request_otherwise(self):
        ledger = []
        calls = []

        def request(_client):
            calls.append(1)
            if len(calls) == 3:
                raise ConnectionResetError
            return [len(calls)]

        for keep_open, opened in ((True, 2), (False, 5)):
            ledger.clear()
            calls.clear()
            with mock.patch.object(MODULE, "time", Clock()):
                records = MODULE.measure(lambda: Link(ledger), request, 5, 1, keep_open=keep_open)
            self.assertEqual([record.get("error") for record in records],
                             [None, None, "ConnectionResetError", None, None])
            self.assertEqual((ledger.count("open"), ledger.count("close")), (opened, opened), keep_open)

    def test_rates_above_ten_per_second_are_refused(self):
        with contextlib.redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit) as raised:
            MODULE.main(["--rates", "1,11"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("at most 10", error.getvalue())

    def test_every_kind_host_connection_and_rate_is_run_and_written(self):
        clock = Clock()

        class Legacy:
            def __init__(self, host, port, timeout_seconds=5.0, *, handshake_profile="login_one"):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def quotes(self, stocks):
                clock.now += 0.01
                return [{"code": code, "price": clock.now} for _market, code in stocks]

        class Mac(Legacy):
            batch_quotes = Legacy.quotes

        def page(_client):
            return [{"market": 0, "code": "000001"}, {"market": 1, "code": "600000"}]

        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "cadence.json"
            with (contextlib.redirect_stderr(io.StringIO()) as summary, mock.patch.object(MODULE, "time", clock),
                  mock.patch.object(MODULE, "all_a_page", page), mock.patch.object(MODULE.tdx_protocol, "TdxClient", Legacy),
                  mock.patch.object(MODULE.tdx_mac, "TdxMacClient", Mac)):
                code = MODULE.main(["--hosts", "1.1.1.1:7709,2.2.2.2:7709", "--rates", "1,2", "--seconds", "2",
                                    "--output", str(output)])
            payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertEqual(payload["symbols"], ["000001.SZ", "600000.SH"])
        legacy = ["1.1.1.1:7709", "2.2.2.2:7709"]
        macs = [f"{host}:{port}" for host, port in MODULE.tdx_mac.configured_hosts()]
        expected = [(kind, host, connection, rate)
                    for kind, hosts in (("legacy_0x054b_all_a_page", legacy), ("mac_0x122b_batch", macs),
                                        ("legacy_0x053e_quote", legacy))
                    for host in hosts for connection in ("kept_open", "new_per_request") for rate in (1.0, 2.0)]
        self.assertEqual([(run["kind"], run["host"], run["connection"], run["rate"]) for run in payload["runs"]],
                         expected)
        quote_run = next(run for run in payload["runs"]
                         if (run["kind"], run["connection"], run["rate"]) == ("legacy_0x053e_quote", "kept_open", 2.0))
        self.assertEqual((quote_run["summary"]["requests"], quote_run["summary"]["failures"]), (4, 0))
        self.assertGreater(quote_run["summary"]["digest_changes"], 0, "the fake host answers fresh data")
        self.assertEqual(summary.getvalue().count("\n"), len(expected))


if __name__ == "__main__":
    unittest.main()
