import unittest

from app.large_order_confirmation import (
    confirm, confirm_candidates, page_payload, parse_series, request,
)

# Verbatim from the gateway for 002724 on 2026-09-18.
PAYLOAD = {"dadanjinge": [["09:30", -1221521], ["09:31", -890289], ["09:36", 1261234],
                          ["09:37", 6026100], ["09:38", 5440671]], "code": "002724"}


class ParseSeriesTests(unittest.TestCase):
    def test_the_vendor_series_is_read_in_order(self):
        series = parse_series(PAYLOAD)
        self.assertEqual(len(series), 5)
        self.assertEqual(series[0], ("09:30", -1221521.0))

    def test_a_malformed_point_is_dropped_not_zeroed(self):
        # A zero standing in for an unreadable figure reads as balanced flow.
        series = parse_series({"dadanjinge": [["09:30", "x"], ["09:31", 100.0], "junk", ["", 5]]})
        self.assertEqual(series, [("09:31", 100.0)])

    def test_an_empty_payload_yields_no_series(self):
        self.assertEqual(parse_series({}), [])


class ConfirmTests(unittest.TestCase):
    """Cumulative level and the window's push must both agree."""

    def test_an_inflow_name_pushing_up_is_confirmed(self):
        result = confirm("inflow", [("09:30", -1_200_000.0), ("09:37", 6_000_000.0)])
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["cumulative_net"], 6_000_000.0)
        self.assertEqual(result["window_push"], 7_200_000.0)

    def test_a_one_minute_uptick_inside_deep_outflow_is_not_a_turn(self):
        # Level still negative: the name has not turned, whatever the push.
        result = confirm("inflow", [("09:30", -9_000_000.0), ("09:37", -7_000_000.0)])
        self.assertFalse(result["confirmed"])

    def test_an_outflow_name_pushing_down_is_confirmed(self):
        result = confirm("outflow", [("13:00", -1_000_000.0), ("13:15", -8_000_000.0)])
        self.assertTrue(result["confirmed"])

    def test_the_push_uses_the_window_not_the_last_value(self):
        # The series is cumulative; the last value still carries the morning.
        series = [("09:30", 50_000_000.0)] + [(f"13:{i:02d}", 50_000_000.0 - i * 1_000_000.0) for i in range(16)]
        result = confirm("inflow", series, recent_minutes=15)
        self.assertLess(result["window_push"], 0)
        self.assertFalse(result["confirmed"])

    def test_flow_inside_the_noise_band_is_inconclusive_not_a_rejection(self):
        result = confirm("inflow", [("09:30", 1000.0), ("09:40", 2000.0)])
        self.assertEqual(result["status"], "inconclusive")
        self.assertFalse(result["confirmed"])

    def test_no_series_reports_unavailable(self):
        self.assertEqual(confirm("inflow", [])["status"], "unavailable")


class RequestTests(unittest.TestCase):
    def test_the_symbol_is_sent_without_its_exchange_suffix(self):
        self.assertEqual(request("002724.SZ")["params"]["StockID"], "002724")

    def test_the_documented_target_and_action_are_used(self):
        built = request("002724.SZ")
        self.assertEqual(built["target"], "longhu_quote")
        self.assertEqual(built["params"]["a"], "GetStockDaDanTrendIncremental")

    def test_the_gateway_envelope_is_unwrapped(self):
        self.assertEqual(page_payload({"pages": [{"payload": PAYLOAD}]})["code"], "002724")
        self.assertEqual(page_payload({}), {})


class ConfirmCandidatesTests(unittest.TestCase):
    def test_each_candidate_gets_one_call_and_a_verdict(self):
        calls = []

        def fetch(built):
            calls.append(built["params"]["StockID"])
            return {"pages": [{"payload": PAYLOAD}]}

        result = confirm_candidates(
            [{"symbol": "002724.SZ", "direction": "inflow"},
             {"symbol": "002156.SZ", "direction": "inflow"}], fetch)
        self.assertEqual(calls, ["002724", "002156"])
        self.assertTrue(result[0]["large_order"]["confirmed"])

    def test_a_failing_call_marks_the_candidate_unconfirmed_rather_than_dropping_it(self):
        # A gateway problem must stay visible as unconfirmed evidence instead
        # of silently shortening the list.
        def fetch(_built):
            raise RuntimeError("gateway timeout")

        result = confirm_candidates([{"symbol": "002724.SZ", "direction": "inflow"}], fetch)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["large_order"]["status"], "failed")
        self.assertFalse(result[0]["large_order"]["confirmed"])


if __name__ == "__main__":
    unittest.main()
