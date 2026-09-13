import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load_relay(name):
    relay_path = Path(__file__).resolve().parents[1] / "wechat-biz-relay.py"
    spec = importlib.util.spec_from_file_location(name, relay_path)
    relay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay)
    return relay


class DeliveryBackoffTests(unittest.TestCase):
    def test_healthy_account_is_never_delayed(self):
        relay = _load_relay("wechat_biz_relay_backoff_healthy")
        self.assertEqual(relay.next_retry_backoff_seconds(0), 0.0)
        self.assertTrue(relay.sink_delivery_allowed("gh_ok", {}, now=1_000.0))

    def test_backoff_grows_exponentially_and_caps(self):
        relay = _load_relay("wechat_biz_relay_backoff_growth")
        self.assertEqual(relay.next_retry_backoff_seconds(1, base=10.0, cap=300.0), 10.0)
        self.assertEqual(relay.next_retry_backoff_seconds(2, base=10.0, cap=300.0), 20.0)
        self.assertEqual(relay.next_retry_backoff_seconds(3, base=10.0, cap=300.0), 40.0)
        # Growth is capped so a persistent outage never delays a retry
        # past a bounded ceiling.
        self.assertEqual(relay.next_retry_backoff_seconds(20, base=10.0, cap=300.0), 300.0)

    def test_delivery_blocked_until_backoff_window_elapses(self):
        relay = _load_relay("wechat_biz_relay_backoff_window")
        backoff_until = {"gh_failing": 1_100.0}
        self.assertFalse(relay.sink_delivery_allowed("gh_failing", backoff_until, now=1_050.0))
        self.assertTrue(relay.sink_delivery_allowed("gh_failing", backoff_until, now=1_100.0))
        # An account with no recorded failure is unaffected by another
        # account's backoff window.
        self.assertTrue(relay.sink_delivery_allowed("gh_other", backoff_until, now=1_050.0))


if __name__ == "__main__":
    unittest.main()
