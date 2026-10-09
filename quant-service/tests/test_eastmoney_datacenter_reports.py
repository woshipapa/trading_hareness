"""The datacenter's whole-or-nothing paging and the two date-keyed reports added for decision 0005."""

from __future__ import annotations

import asyncio
import unittest
from datetime import date
from unittest.mock import patch

from app.datasources.sources import eastmoney_datacenter as datacenter


def pages(*page_rows, count):
    calls = []

    async def request(_method, _url, *, params, timeout_seconds):
        calls.append(params)
        index = int(params["pageNumber"]) - 1
        return {"code": 0, "result": {"count": count, "pages": len(page_rows), "data": page_rows[index]}}

    return request, calls


class FetchReportTests(unittest.TestCase):
    def test_a_complete_report_is_returned_across_pages(self):
        request, calls = pages([{"a": 1}, {"a": 2}], [{"a": 3}], count=3)
        with patch.object(datacenter, "request_json", request):
            rows = asyncio.run(datacenter.fetch_report(
                "suspension", extra_filter=datacenter.suspension_filter(date(2026, 10, 9)), require_complete=True))
        self.assertEqual([row["a"] for row in rows], [1, 2, 3])
        self.assertEqual(calls[0]["filter"], "(MARKET=\"全部\")(DATETIME='2026-10-09')")
        self.assertEqual(calls[0]["reportName"], "RPT_CUSTOM_SUSPEND_DATA_INTERFACE")

    def test_a_short_answer_is_refused_when_completeness_is_required(self):
        request, _calls = pages([{"a": 1}, {"a": 2}], count=5)
        with patch.object(datacenter, "request_json", request):
            with self.assertRaisesRegex(ValueError, "returned 2 of 5 rows"):
                asyncio.run(datacenter.fetch_report(
                    "disclosure_schedule", extra_filter=datacenter.period_filter(date(2026, 6, 30)),
                    require_complete=True))

    def test_without_the_flag_a_short_answer_is_returned_as_before(self):
        request, _calls = pages([{"a": 1}], count=5)
        with patch.object(datacenter, "request_json", request):
            self.assertEqual(len(asyncio.run(datacenter.fetch_report("disclosure_schedule"))), 1)

    def test_period_filter_names_the_quarter_end(self):
        self.assertEqual(datacenter.period_filter(date(2026, 6, 30)), "(REPORT_DATE='2026-06-30')")
        self.assertEqual(datacenter.REPORTS["disclosure_schedule"].report_name, "RPT_PUBLIC_BS_APPOIN")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
