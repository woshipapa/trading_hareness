import contextlib
import importlib.util
import io
import json
import socket
import sys
import tempfile
import types
import unittest
from datetime import date
from pathlib import Path
from unittest import mock


class FreeProviderError(RuntimeError):
    pass


# CI installs only pytest and the real reader needs httpx, so the script is loaded against a stand-in for its module.
READER_NAME = "app.free_market_providers"
READER = types.ModuleType(READER_NAME)
READER.FreeProviderError, READER.cninfo_announcements = FreeProviderError, None
SCRIPT = Path(__file__).with_name("probe-tdx-disclosure-timing.py")
SPEC = importlib.util.spec_from_file_location("probe_tdx_disclosure_timing", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
REAL_READER = sys.modules.get(READER_NAME)
sys.modules[READER_NAME] = READER
try:
    SPEC.loader.exec_module(MODULE)
finally:
    if REAL_READER is None:
        del sys.modules[READER_NAME]
    else:
        sys.modules[READER_NAME] = REAL_READER

TdxProtocolError = MODULE.tdx_protocol.TdxProtocolError
START = 1000.0
HOST = "1.2.3.4:7709/login_one"


def tipinfo(*rows: str) -> bytes:
    """tipinfo.dat lines as served: 22 columns each (scripts/data/tdx_tipinfo_2026-10-10_mac.json)."""
    return b"".join(("|".join(row.split("|") + [""] * (22 - len(row.split("|")))) + "\n").encode() for row in rows)


#: tipinfo.dat as three polls saw it: 000001 reports its Q3 and 600519 appears (poll 2), 000002 follows (poll 3).
SNAPSHOTS = [
    tipinfo("0|000001|20260630|1.24|20260815", "1|600000|20260630|0.90|20260820", "0|000002|20260630|0.10|20260828"),
    tipinfo("0|000001|20260930|1.30|20261012", "1|600000|20260630|0.90|20260820", "0|000002|20260630|0.10|20260828",
            "1|600519|20260630|5.00|20260730"),
    tipinfo("0|000001|20260930|1.30|20261012", "1|600000|20260630|0.90|20260820", "0|000002|20260930|0.10|20261012",
            "1|600519|20260630|5.00|20260730"),
]


class Clock:
    """Time that moves only when the code sleeps."""

    def __init__(self):
        self.now = START

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class DisclosureTimingTests(unittest.TestCase):
    def setUp(self):
        blocked = mock.patch.object(socket.socket, "connect", side_effect=AssertionError("tests must not connect"))
        blocked.start()
        self.addCleanup(blocked.stop)

    def run_main(self, snapshots, cninfo, *argv):
        """main() with each poll answered by the next of ``snapshots`` (an exception there is raised by the poll),
        the parser's own name for column 4 removed, the cninfo reader replaced and the poll time "t<seconds since
        the start>": the output file, the clock, the exit status and the KeyError that ended the run, if any (the
        one a test raises)."""
        served = iter(snapshots)
        clock = Clock()
        real = MODULE.tdx_zhb_extras.parse_tipinfo

        def call_sync(operation, **_kwargs):
            data = next(served)
            if isinstance(data, Exception):
                raise data
            return {"tipinfo.dat": data}, HOST

        def positions_only(data):
            return [{"raw_fields": row["raw_fields"]} for row in real(data)]

        status = error = None
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "timing.json"
            with (contextlib.redirect_stderr(io.StringIO()), mock.patch.object(MODULE, "time", clock),
                  mock.patch.object(MODULE.tdx_protocol, "call_sync", call_sync),
                  mock.patch.object(MODULE.tdx_zhb_extras, "parse_tipinfo", positions_only),
                  mock.patch.object(MODULE, "now", lambda: f"t{int(clock.now - START)}"),
                  mock.patch.object(MODULE, "cninfo_announcements", cninfo),
                  mock.patch.object(MODULE, "configured_rate_limit", lambda key: 60_000)):
                try:
                    status = MODULE.main(["--output", str(output), *argv])
                except KeyError as raised:
                    error = raised
            return json.loads(output.read_text(encoding="utf-8")), clock, status, error

    def test_each_poll_logs_the_rows_the_poll_before_did_not_have_and_each_is_looked_up_on_cninfo(self):
        calls = []

        async def cninfo(symbol, start, end, *, page_size=30, max_pages=3):
            calls.append((symbol, start, end, max_pages))
            if symbol == "000002.SZ":
                raise FreeProviderError("rate_limited:cninfo_free")
            title = "平安银行2026年第三季度报告" if symbol == "000001.SZ" else "2026年半年度报告摘要"
            return [{"title": title, "published_at": "2026-10-11T16:00:00+00:00"},
                    {"title": "关于召开股东会的通知", "published_at": "2026-10-12T02:30:00+00:00"}]

        payload, clock, status, error = self.run_main(SNAPSHOTS, cninfo, "--polls", "3", "--interval", "10")
        self.assertEqual((status, error, payload["errors"]), (0, None, 0))
        self.assertNotIn("stopped", payload)
        polls = payload["polls"]
        self.assertEqual(clock.now - START, 1200.0, "polls start 10 minutes apart")
        self.assertEqual([(poll["rows"], poll["host"]) for poll in polls], [(3, HOST), (4, HOST), (4, HOST)])
        self.assertIsNone(polls[0]["new"], "the first poll is the baseline")
        self.assertEqual(polls[1]["new"], [{"symbol": "000001.SZ", "period": "20260930", "date": "20261012"},
                                           {"symbol": "600519.SH", "period": "20260630", "date": "20260730"}])
        self.assertEqual(polls[2]["new"], [{"symbol": "000002.SZ", "period": "20260930", "date": "20261012"}])
        self.assertEqual(calls, [("000001.SZ", date(2026, 10, 11), date(2026, 10, 13), 1),
                                 ("000002.SZ", date(2026, 10, 11), date(2026, 10, 13), 1),
                                 ("600519.SH", date(2026, 7, 29), date(2026, 7, 31), 1)])
        first, second, third = payload["lookups"]
        self.assertEqual([poll["polled_at"] for poll in polls], ["t0", "t600", "t1200"])
        self.assertEqual(first["appeared_between"], ["t0", "t600"])
        self.assertEqual(second["appeared_between"], ["t600", "t1200"])
        self.assertEqual(first["announcements"], [
            {"title": "平安银行2026年第三季度报告", "matches_period": True, "published_at": "2026-10-12T00:00:00+08:00"},
            {"title": "关于召开股东会的通知", "matches_period": False, "published_at": "2026-10-12T10:30:00+08:00"}])
        self.assertEqual(second["lookup_error"], "rate_limited:cninfo_free")
        self.assertTrue(third["announcements"][0]["matches_period"], "the summary title names the half-year report")

    def test_a_row_that_comes_back_keeps_the_bracket_of_its_first_appearance(self):
        async def cninfo(symbol, start, end, *, page_size=30, max_pages=3):
            return []

        without, with_row = SNAPSHOTS[0], SNAPSHOTS[0] + tipinfo("1|600519|20260630|5.00|20260730")
        payload, _, _, _ = self.run_main([without, with_row, without, with_row], cninfo, "--polls", "4")
        self.assertEqual([bool(poll["new"]) for poll in payload["polls"][1:]], [True, False, True])
        self.assertEqual([entry["appeared_between"] for entry in payload["lookups"]], [["t0", "t600"]])

    def test_a_tipinfo_row_without_a_date_fails_its_poll_and_the_polling_goes_on(self):
        async def cninfo(symbol, start, end, *, page_size=30, max_pages=3):
            return []

        sentinel = tipinfo("0|000001|20260630|1.24|20260815", "0|000002|20260930|0.10|0")
        payload, _, status, _ = self.run_main([SNAPSHOTS[0], sentinel, SNAPSHOTS[1]], cninfo, "--polls", "3")
        self.assertEqual((status, payload["errors"], payload["polls"][1]["error"]), (0, 1, "TdxFileError"))
        self.assertEqual(sorted(row["symbol"] for row in payload["polls"][2]["new"]), ["000001.SZ", "600519.SH"])

    def test_a_failed_poll_is_an_entry_and_the_next_good_poll_compares_with_the_last_good_one(self):
        async def cninfo(symbol, start, end, *, page_size=30, max_pages=3):
            return []

        down = TdxProtocolError("no TDX host answered: " + "h" * 300)
        polls = [SNAPSHOTS[0], down, SNAPSHOTS[1], down, down, down, down, SNAPSHOTS[1], down]
        payload, _, status, error = self.run_main(polls, cninfo, "--polls", "9")
        self.assertEqual((status, error, payload["errors"], len(payload["polls"])), (0, None, 6, 9),
                         "six failed, never five in a row")
        self.assertNotIn("stopped", payload)
        self.assertEqual(payload["polls"][1], {"polled_at": "t600", "error": "TdxProtocolError",
                                               "message": ("no TDX host answered: " + "h" * 300)[:200]})
        self.assertEqual([row["symbol"] for row in payload["polls"][2]["new"]], ["000001.SZ", "600519.SH"])
        self.assertEqual(payload["polls"][7]["new"], [])
        self.assertEqual([entry["appeared_between"] for entry in payload["lookups"]], [["t0", "t1200"]] * 2,
                         "the bracket of a row spans the poll that failed")

    def test_failed_polls_in_a_row_stop_the_polling_say_so_and_the_lookups_still_run(self):
        looked_up = []

        async def cninfo(symbol, start, end, *, page_size=30, max_pages=3):
            looked_up.append(symbol)
            return []

        down = TdxProtocolError("no TDX host answered")
        for extra, in_a_row in (((), 5), (("--max-consecutive-errors", "2"), 2)):
            looked_up.clear()
            polls = [SNAPSHOTS[0], SNAPSHOTS[1], *[down] * in_a_row]
            payload, _, status, error = self.run_main(polls, cninfo, "--polls", "20", *extra)
            self.assertEqual((status, error, payload["errors"]), (1, None, in_a_row))
            self.assertEqual(payload["stopped"], f"{in_a_row} polls in a row failed")
            self.assertEqual(len(payload["polls"]), 2 + in_a_row)
            self.assertEqual(looked_up, ["000001.SZ", "600519.SH"], "the rows found before the polling stopped")

    def test_an_exception_of_another_kind_ends_the_run_and_the_polls_so_far_stay_in_the_output_file(self):
        async def cninfo(*_args, **_kwargs):
            raise AssertionError("no lookup while polling")

        payload, _, status, error = self.run_main([SNAPSHOTS[0], SNAPSHOTS[1], KeyError("tipinfo.dat")], cninfo,
                                                  "--polls", "3")
        self.assertEqual((status, str(error)), (None, "'tipinfo.dat'"))
        self.assertEqual(len(payload["polls"]), 2)
        self.assertNotIn("lookups", payload)


if __name__ == "__main__":
    unittest.main()
