"""``python -m app.datasources probe``: one call to the adapter a binding names, whatever the binding's status."""

import contextlib
import io
import json
import unittest
from datetime import datetime, timezone
from unittest import mock

from app.datasources import __main__ as cli
from app.datasources import catalog
from app.datasources.contracts import BINDING_STATES, Binding, CapabilityEvidence
from app.datasources.sources import tdx_legacy_misc
from app.datasources.sources.tdx_protocol import TdxProtocolError

ADAPTER = "app/datasources/sources/tdx_legacy_misc.py:fetch_index_overview"


def probe(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(["probe", *argv])
    return code, json.loads(out.getvalue())


class ProbeTests(unittest.TestCase):
    def test_prints_one_record_of_the_single_call(self):
        calls = []
        observed = datetime(2026, 10, 12, 1, 31, tzinfo=timezone.utc)

        async def answer(**params):
            calls.append(params)
            return CapabilityEvidence([{"close": 4000.0 + i} for i in range(cli.SAMPLE_ROWS + 3)], coverage=0.5,
                                      available_at_min=observed, available_at_max=observed,
                                      warnings=("tdx_host=1.2.3.4:7709/login_one",))

        with mock.patch.object(tdx_legacy_misc, "fetch_index_overview", answer):
            code, record = probe("tdx_public", "quote.index_overview", "--params", '{"symbol": "000300.SH"}')
        self.assertEqual((code, calls), (0, [{"symbol": "000300.SH"}]))
        self.assertEqual((record["source"], record["capability"], record["params"], record["error"]),
                         ("tdx_public", "quote.index_overview", {"symbol": "000300.SH"}, None))
        self.assertEqual((record["rows"], len(record["sample"]), record["coverage"]), (cli.SAMPLE_ROWS + 3, cli.SAMPLE_ROWS, 0.5))
        self.assertEqual(record["warnings"], ["tdx_host=1.2.3.4:7709/login_one"])
        self.assertEqual((record["available_at_min"], record["effective_at_min"]), ("2026-10-12T01:31:00+00:00", None))
        self.assertLessEqual(record["started_utc"], record["finished_utc"])

    def test_plain_rows_are_an_answer_without_coverage_or_clocks(self):
        async def answer(**_params):
            return [{"close": 4000.0}]

        with mock.patch.object(tdx_legacy_misc, "fetch_index_overview", answer):
            code, record = probe("tdx_public", "quote.index_overview")
        self.assertEqual((code, record["rows"], record["coverage"], record["warnings"]), (0, 1, None, []))

    def test_an_adapter_that_raises_is_reported_with_a_non_zero_exit(self):
        async def answer(**_params):
            raise TdxProtocolError("no TDX host answered: 1.2.3.4:login_one:OSError")

        with mock.patch.object(tdx_legacy_misc, "fetch_index_overview", answer):
            code, record = probe("tdx_public", "quote.index_overview")
        self.assertEqual(code, 1)
        self.assertEqual(record["error"], "TdxProtocolError: no TDX host answered: 1.2.3.4:login_one:OSError")
        self.assertNotIn("rows", record)      # a failed call is not an empty answer

    def test_every_status_is_probed_and_a_binding_without_a_function_is_refused(self):
        async def answer(**_params):
            return [{"close": 1.0}]

        for status in BINDING_STATES:
            with self.subTest(status=status), mock.patch.object(tdx_legacy_misc, "fetch_index_overview", answer), \
                    mock.patch.object(catalog, "BINDINGS", (Binding("tdx_public", "quote.index_overview", 80, status, adapter=ADAPTER),)):
                self.assertEqual(probe("tdx_public", "quote.index_overview")[0], 0)
        for adapter in (None, "app/free_market_providers.py"):
            with self.subTest(adapter=adapter), contextlib.redirect_stderr(io.StringIO()), \
                    mock.patch.object(catalog, "BINDINGS", (Binding("tdx_public", "quote.index_overview", 80, "unsupported", adapter=adapter),)):
                with self.assertRaises(SystemExit) as refused:
                    cli.main(["probe", "tdx_public", "quote.index_overview"])
                self.assertEqual(refused.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
