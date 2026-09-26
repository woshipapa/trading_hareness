"""轮询时间窗的契约：午间休市(11:30-13:00)必须继续监听三个来源。"""
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import itougu_neican_relay as relay


def cst(day, hour, minute):
    return datetime(2026, 9, day, hour, minute, tzinfo=relay.CST)


TUESDAY, SATURDAY = 8, 12   # 2026-09-08 是周二，2026-09-12 是周六


class MiddayWindowTests(unittest.TestCase):
    def test_midday_break_keeps_polling(self):
        for hour, minute in ((11, 31), (12, 0), (12, 59)):
            with self.subTest(time=(hour, minute)):
                should_poll, _ = relay.poll_plan(cst(TUESDAY, hour, minute), interval=15,
                                                 off_hours_interval=600, trading_hours_only=True)
                self.assertTrue(should_poll)

    def test_midday_break_is_recognized_only_between_the_two_sessions(self):
        self.assertFalse(relay.in_midday_break(cst(TUESDAY, 11, 30)))   # 上午收盘那一分钟仍属盘中
        self.assertTrue(relay.in_midday_break(cst(TUESDAY, 11, 31)))
        self.assertTrue(relay.in_midday_break(cst(TUESDAY, 12, 59)))
        self.assertFalse(relay.in_midday_break(cst(TUESDAY, 13, 0)))    # 下午开盘
        self.assertFalse(relay.in_midday_break(cst(SATURDAY, 12, 0)))   # 周末没有午休概念

    def test_midday_uses_the_session_cadence_by_default(self):
        _, sleep_seconds = relay.poll_plan(cst(TUESDAY, 12, 0), interval=15,
                                           off_hours_interval=600, trading_hours_only=True)
        self.assertEqual(sleep_seconds, 15)

    def test_midday_cadence_can_be_dialed_down_independently(self):
        _, sleep_seconds = relay.poll_plan(cst(TUESDAY, 12, 0), interval=15, midday_interval=60,
                                           off_hours_interval=600, trading_hours_only=True)
        self.assertEqual(sleep_seconds, 60)
        _, session_sleep = relay.poll_plan(cst(TUESDAY, 10, 0), interval=15, midday_interval=60,
                                           off_hours_interval=600, trading_hours_only=True)
        self.assertEqual(session_sleep, 15)


class SessionWindowTests(unittest.TestCase):
    def test_both_sessions_poll_at_the_trading_interval(self):
        for hour, minute in ((9, 30), (11, 30), (13, 0), (15, 0)):
            with self.subTest(time=(hour, minute)):
                should_poll, sleep_seconds = relay.poll_plan(cst(TUESDAY, hour, minute), interval=15,
                                                             off_hours_interval=600, trading_hours_only=True)
                self.assertTrue(should_poll)
                self.assertEqual(sleep_seconds, 15)

    def test_after_close_still_polls_but_slowly(self):
        should_poll, sleep_seconds = relay.poll_plan(cst(TUESDAY, 16, 0), interval=15,
                                                     off_hours_interval=600, trading_hours_only=True)
        self.assertTrue(should_poll)
        self.assertEqual(sleep_seconds, 600)

    def test_before_the_open_stays_quiet_under_trading_hours_only(self):
        should_poll, sleep_seconds = relay.poll_plan(cst(TUESDAY, 7, 0), interval=15,
                                                     off_hours_interval=600, trading_hours_only=True)
        self.assertFalse(should_poll)
        self.assertEqual(sleep_seconds, 600)

    def test_weekend_stays_quiet_under_trading_hours_only(self):
        should_poll, _ = relay.poll_plan(cst(SATURDAY, 12, 0), interval=15,
                                         off_hours_interval=600, trading_hours_only=True)
        self.assertFalse(should_poll)

    def test_without_the_flag_every_hour_polls(self):
        for day, hour in ((TUESDAY, 3), (SATURDAY, 12)):
            with self.subTest(day=day, hour=hour):
                should_poll, _ = relay.poll_plan(cst(day, hour, 0), interval=15,
                                                 off_hours_interval=600, trading_hours_only=False)
                self.assertTrue(should_poll)

    def test_off_hours_sleep_keeps_its_floor(self):
        _, sleep_seconds = relay.poll_plan(cst(TUESDAY, 16, 0), interval=15,
                                           off_hours_interval=5, trading_hours_only=True)
        self.assertEqual(sleep_seconds, 30)


if __name__ == "__main__":
    unittest.main()
