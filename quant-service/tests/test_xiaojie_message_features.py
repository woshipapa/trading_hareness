"""Point-in-time group-message features: extraction, storage and display."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.xiaojie_message_features import (
    MARKET_SYMBOL, feature_payload, features, ingest, latest_instructor_line, stated_at, store,
)

NAMES = {"跨境通": "002640.SZ", "华海诚科": "688535.SH", "中国巨石": "600176.SH"}


def _record(text, *, source_key="relay_9b8fe9b2294641248c26e1405ac063b7", message_id="om_1", msg_type="text"):
    return {"source_key": source_key, "source_chat_id": "oc_1", "source_message_id": message_id,
            "source_create_time": 1_790_000_000_000, "created_at": "2026-09-17T03:09:05+00:00",
            "message": {"msg_type": msg_type, "body": {"content": json.dumps({"text": text}, ensure_ascii=False)}}}


REPLY = ("【讲师】小杰\n回复 @某用户：跨境通是潜龙出海吗？\n--------\n"
         "形态理论上符合，但基本面不行，不给确认。\n—— 2026-09-17 11:08:59")


class FeaturePayloadTests(unittest.TestCase):
    def test_a_reply_keeps_both_times_codes_and_the_instructor_text(self):
        payload = feature_payload(_record(REPLY), NAMES)
        self.assertEqual(payload["available_at"], "2026-09-17T03:09:05+00:00")   # ledger receive time
        self.assertEqual(payload["stated_at"], "2026-09-17T03:08:59+00:00")      # author time, review only
        self.assertEqual(payload["role"], "instructor_reply")
        self.assertEqual(payload["stance"], "not_confirmed_or_risk")
        self.assertEqual(payload["symbols"], ["002640.SZ"])
        self.assertEqual(payload["excerpt"], "形态理论上符合，但基本面不行，不给确认。")
        self.assertEqual(payload["mentions"], {})        # named only in the question, which is not kept
        self.assertNotIn("某用户", json.dumps(payload, ensure_ascii=False))
        self.assertIn("qianlong", payload["tags"])
        self.assertEqual(payload["live_effect"], "none")

    def test_a_statement_drops_handles_it_mentions(self):
        payload = feature_payload(_record("【讲师】小杰 @张三 中国巨石沿5日线持有", source_key="xiaojie"), NAMES)
        self.assertEqual(payload["role"], "instructor_statement")
        self.assertNotIn("张三", payload["excerpt"])
        self.assertIn("中国巨石沿5日线持有", payload["mentions"]["600176.SH"])

    def test_participant_text_is_never_kept(self):
        payload = feature_payload(_record("中国巨石还能拿吗"), NAMES)
        self.assertEqual(payload["role"], "participant_message")
        self.assertEqual(payload["symbols"], ["600176.SH"])
        self.assertIsNone(payload["excerpt"])
        self.assertEqual(payload["mentions"], {})

    def test_a_night_report_keeps_the_passage_around_each_stock(self):
        report = "小杰:" + "今天指数缩量震荡。" * 20 + "华海诚科二次放量反包，是买点。" + "明天关注量能。" * 20
        payload = feature_payload(_record(report, source_key="xiaojie"), NAMES)
        self.assertEqual(payload["role"], "night_report_or_review")
        passage = payload["mentions"]["688535.SH"]
        self.assertIn("华海诚科二次放量反包", passage)
        self.assertLessEqual(len(passage), 60 * 2 + len("华海诚科"))

    def test_images_and_system_messages_are_not_features(self):
        self.assertIsNone(feature_payload(_record("x", msg_type="image"), NAMES))

    def test_author_time_parses_or_stays_absent(self):
        self.assertEqual(stated_at("…—— 2026-09-22 15:38:59"), "2026-09-22T07:38:59+00:00")
        self.assertIsNone(stated_at("没有时间"))


class StoreTests(unittest.TestCase):
    def test_one_row_per_stock_and_market_commentary_under_its_own_key(self):
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = {"observation_id": 1}
        two_stocks = {**feature_payload(_record(REPLY), NAMES), "symbols": ["002640.SZ", "688535.SH"]}
        market = {**two_stocks, "source_message_id": "om_2", "symbols": []}
        self.assertEqual(store(connection, [two_stocks, market]), 3)
        symbols = [call.args[1][2] for call in connection.execute.call_args_list]
        self.assertEqual(symbols, ["002640.SZ", "688535.SH", MARKET_SYMBOL])
        effective, available = connection.execute.call_args_list[0].args[1][3:5]
        self.assertEqual((effective, available), (two_stocks["stated_at"], two_stocks["available_at"]))
        self.assertIn("ON CONFLICT", connection.execute.call_args_list[0].args[0])

    def test_ingest_counts_what_it_skipped(self):
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = [
            {"symbol": code, "name": name} for name, code in NAMES.items()]
        connection.execute.return_value.fetchone.return_value = {"observation_id": 1}
        report = ingest(connection, [_record(REPLY), _record("img", message_id="om_3", msg_type="image")])
        self.assertEqual((report["received"], report["non_text"], report["messages"], report["with_stock"]),
                         (2, 1, 1, 1))


class ReadAndDisplayTests(unittest.TestCase):
    def test_reads_apply_the_availability_rule(self):
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = []
        as_of = datetime(2026, 9, 22, 2, 0, tzinfo=timezone.utc)
        features(connection, as_of=as_of, symbol="002640.SZ", instructor_only=True)
        sql, params = connection.execute.call_args.args
        self.assertIn("available_at <= %s", sql)
        self.assertEqual(params[2], as_of)

    def test_the_alert_line_quotes_the_instructor_on_this_stock(self):
        payload = feature_payload(_record(REPLY), NAMES)
        line = latest_instructor_line([{"symbol": "002640.SZ", **payload}], "002640.SZ")
        self.assertEqual(line, "小杰群聊 09-17 11:09 讲师回复「未确认/风险」：形态理论上符合，但基本面不行，不给确认。")
        participant = feature_payload(_record("跨境通怎么看"), NAMES)
        self.assertIsNone(latest_instructor_line([{"symbol": "002640.SZ", **participant}], "002640.SZ"))


if __name__ == "__main__":
    unittest.main()
