import json
import unittest
from datetime import date, datetime
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).parent))
import itougu_perf_report as perf


def item(aid, when, *, name=None, code=None, price=None, deal=None, mkt="sh", position=0.625,
         as_string=False, content="正文"):
    """Build one appendContent record shaped like the Itougu API response."""
    payload = {"appendContentId": aid, "publishTime": when, "content": content}
    if name is None:
        return payload
    detail = {"stockName": name, "stockCode": code, "price": price,
              "mkt": mkt, "position": position, "dealType": deal}
    if as_string:
        payload["simulateOperationJson"] = json.dumps(detail, ensure_ascii=False)
    else:
        payload["stockTransactionDetail"] = detail
    return payload


class TargetParsingTests(unittest.TestCase):
    def test_parses_product_to_chat_mapping(self):
        parsed = perf.parse_targets("pid-a=chat-1,chat-2; pid-b=chat-3")
        self.assertEqual(parsed, {"pid-a": ["chat-1", "chat-2"], "pid-b": ["chat-3"]})

    def test_ignores_blank_entries_and_dedupes_destinations(self):
        parsed = perf.parse_targets(" ;pid-a=chat-1,,chat-1 , chat-2;")
        self.assertEqual(parsed, {"pid-a": ["chat-1", "chat-2"]})

    def test_product_without_destination_is_kept_so_the_run_can_still_report_dry(self):
        self.assertEqual(perf.parse_targets("pid-a"), {"pid-a": []})

    def test_empty_spec_yields_no_targets(self):
        self.assertEqual(perf.parse_targets(""), {})


class TradeExtractionTests(unittest.TestCase):
    def test_reads_both_object_and_json_string_details_and_sorts_oldest_first(self):
        items = [
            item("2", "2026-09-04 10:08", name="神奇制药", code="600613", price=9.16, deal=0),
            item("1", "2026-09-02 09:25", name="常山北明", code="000158", price=13.92, deal=1,
                 mkt="sz", as_string=True),
            item("3", "2026-09-03 18:45"),  # 复盘视频，不是操作
        ]
        trades = perf.extract_trades(items)
        self.assertEqual([t["append_id"] for t in trades], ["1", "2"])
        self.assertEqual(trades[0]["symbol"], "sz000158")
        self.assertEqual(trades[0]["deal"], 1)
        self.assertEqual(trades[1]["price"], 9.16)
        self.assertEqual(trades[1]["at"], datetime(2026, 9, 4, 10, 8, tzinfo=perf.CST))

    def test_skips_records_without_a_usable_price_or_code(self):
        items = [
            item("1", "2026-09-04 10:08", name="缺价", code="600613", price=None, deal=0),
            item("2", "2026-09-04 10:09", name="缺码", code=None, price=1.0, deal=0),
        ]
        self.assertEqual(perf.extract_trades(items), [])

    def test_market_prefix_is_derived_when_upstream_omits_it(self):
        trades = perf.extract_trades([
            item("1", "2026-09-04 10:08", name="四会富仕", code="300852", price=56.64, deal=0, mkt=""),
        ])
        self.assertEqual(trades[0]["symbol"], "sz300852")


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.trades = perf.extract_trades([
            item("1", "2026-08-28 09:50", name="百合花", code="603823", price=69.49, deal=1),
            item("2", "2026-08-31 13:17", name="电广传媒", code="000917", price=7.45, deal=0, mkt="sz"),
            item("3", "2026-09-03 10:05", name="电广传媒", code="000917", price=7.35, deal=1, mkt="sz"),
            item("4", "2026-09-08 09:47", name="长飞光纤", code="601869", price=411.0, deal=0),
        ])

    def test_fifo_pairs_buys_with_later_sells(self):
        book = perf.pair_trades(self.trades)
        self.assertEqual(len(book.closed), 1)
        closed = book.closed[0]
        self.assertEqual(closed["symbol"], "sz000917")
        self.assertAlmostEqual(closed["return_pct"], (7.35 - 7.45) / 7.45 * 100, places=6)
        self.assertEqual(closed["holding_days"], 3)

    def test_sell_without_a_visible_buy_is_reported_separately_not_guessed(self):
        book = perf.pair_trades(self.trades)
        self.assertEqual([t["name"] for t in book.orphan_sells], ["百合花"])

    def test_unmatched_buys_stay_open(self):
        book = perf.pair_trades(self.trades)
        self.assertEqual([t["name"] for t in book.open_positions], ["长飞光纤"])

    def test_repeated_buys_close_in_first_in_first_out_order(self):
        trades = perf.extract_trades([
            item("1", "2026-09-01 10:00", name="X", code="600000", price=10.0, deal=0),
            item("2", "2026-09-02 10:00", name="X", code="600000", price=12.0, deal=0),
            item("3", "2026-09-03 10:00", name="X", code="600000", price=11.0, deal=1),
        ])
        book = perf.pair_trades(trades)
        self.assertAlmostEqual(book.closed[0]["buy"]["price"], 10.0)
        self.assertAlmostEqual(book.open_positions[0]["price"], 12.0)


class AsOfWindowTests(unittest.TestCase):
    def setUp(self):
        self.trades = perf.extract_trades([
            item("1", "2026-09-01 11:20", name="常山北明", code="000158", price=13.26, deal=0, mkt="sz"),
            item("2", "2026-09-02 09:25", name="常山北明", code="000158", price=13.92, deal=1, mkt="sz"),
            item("3", "2026-09-08 09:47", name="长飞光纤", code="601869", price=411.0, deal=0),
        ])

    def test_back_dated_window_excludes_later_operations(self):
        book = perf.pair_trades(perf.trades_through(self.trades, date(2026, 9, 4)))
        self.assertEqual(book.open_positions, [])
        self.assertEqual(len(book.closed), 1)

    def test_no_cutoff_keeps_the_whole_ledger(self):
        self.assertEqual(len(perf.trades_through(self.trades, None)), 3)

    def test_cutoff_is_inclusive_of_the_report_date(self):
        self.assertEqual(len(perf.trades_through(self.trades, date(2026, 9, 8))), 3)


class QuoteTests(unittest.TestCase):
    PAYLOAD = (
        'v_sh601869="1~长飞光纤~601869~416.85~400.51~400.88~156181~87026~69155~416.84~48~416.83~347~'
        '416.82~41~416.81~15~416.80~68~416.85~564~416.86~161~416.87~2~416.88~5~416.89~124~~'
        '20260908161453~16.34~4.08~423.80~397.00~416.85/156181/6453939210~156181~645393~1.62~";\n'
        'v_pv_none="";\n'
    )

    def test_parses_close_previous_close_and_session_timestamp(self):
        quotes = perf.parse_tencent_quotes(self.PAYLOAD)
        self.assertEqual(set(quotes), {"sh601869"})
        q = quotes["sh601869"]
        self.assertAlmostEqual(q["last"], 416.85)
        self.assertAlmostEqual(q["prev_close"], 400.51)
        self.assertEqual(q["session_date"], date(2026, 9, 8))

    def test_malformed_rows_are_dropped_rather_than_defaulted(self):
        quotes = perf.parse_tencent_quotes('v_sh600000="1~缺字段~600000";\nnot a row\n')
        self.assertEqual(quotes, {})

    def test_missing_quote_is_rendered_as_unavailable_instead_of_a_number(self):
        line = perf.position_line({"name": "四会富仕", "symbol": "sz300852", "price": 56.64,
                                   "position": 0.625}, {})
        self.assertIn("行情缺失", line)
        self.assertNotIn("%", line)

    def test_stale_quote_for_the_report_date_is_treated_as_missing(self):
        quotes = {"sz300852": {"last": 55.52, "prev_close": 52.97, "session_date": date(2026, 9, 5)}}
        usable = perf.usable_quotes(quotes, date(2026, 9, 8))
        self.assertEqual(usable, {})


class SessionGuardTests(unittest.TestCase):
    def test_report_is_skipped_when_the_index_did_not_trade_on_the_report_date(self):
        self.assertFalse(perf.is_session_date({"session_date": date(2026, 9, 7)}, date(2026, 9, 8)))
        self.assertTrue(perf.is_session_date({"session_date": date(2026, 9, 8)}, date(2026, 9, 8)))

    def test_missing_index_quote_fails_closed(self):
        self.assertFalse(perf.is_session_date(None, date(2026, 9, 8)))


class ReportShapeTests(unittest.TestCase):
    def setUp(self):
        self.book = perf.pair_trades(perf.extract_trades([
            item("1", "2026-09-04 10:08", name="神奇制药", code="600613", price=9.16, deal=0),
            item("2", "2026-09-07 13:57", name="出版传媒", code="601999", price=7.07, deal=0),
            item("3", "2026-09-08 09:30", name="出版传媒", code="601999", price=7.43, deal=1),
            item("4", "2026-09-08 09:47", name="长飞光纤", code="601869", price=411.0, deal=0),
        ]))
        self.quotes = {
            "sh600613": {"last": 9.91, "prev_close": 9.41, "session_date": date(2026, 9, 8)},
            "sh601869": {"last": 416.85, "prev_close": 400.51, "session_date": date(2026, 9, 8)},
            "sh601999": {"last": 8.02, "prev_close": 7.29, "session_date": date(2026, 9, 8)},
        }

    def test_daily_report_lists_today_operations_closes_and_open_positions(self):
        title, text = perf.format_daily("猎场擒龙内参", date(2026, 9, 8), self.book, self.quotes)
        self.assertIn("2026-09-08", title)
        self.assertIn("出版传媒", text)
        self.assertIn("+5.09%", text)          # 当日平仓收益
        self.assertIn("长飞光纤", text)          # 持仓浮动
        self.assertIn("+1.42%", text)
        self.assertNotIn("神奇制药 9.16", text)  # 早于当日的建仓不进"今日操作"

    def test_daily_report_states_no_operations_instead_of_inventing_one(self):
        empty = perf.pair_trades([])
        _, text = perf.format_daily("猎场擒龙内参", date(2026, 9, 8), empty, {})
        self.assertIn("今日无操作", text)

    def test_weekly_report_covers_the_iso_week_and_aggregates_only_its_closes(self):
        title, text = perf.format_weekly("猎场擒龙内参", date(2026, 9, 11), self.book, self.quotes)
        self.assertIn("2026-09-07", title)      # 周一
        self.assertIn("2026-09-11", title)      # 周五
        self.assertIn("胜率", text)
        self.assertIn("出版传媒", text)

    def test_weekly_window_is_monday_to_friday(self):
        self.assertEqual(perf.week_window(date(2026, 9, 9)), (date(2026, 9, 7), date(2026, 9, 11)))

    def test_summary_reports_win_rate_and_average_without_compounding_claims(self):
        stats = perf.summarize([c for c in self.book.closed])
        self.assertEqual(stats["count"], 1)
        self.assertEqual(stats["wins"], 1)
        self.assertAlmostEqual(stats["avg_pct"], 5.09, places=2)


class DeliveryTests(unittest.TestCase):
    def test_every_destination_is_attempted_and_deduped(self):
        sent = []
        delivered = perf.deliver_to_chats(["chat-a", "chat-a", "chat-b"], "t", "body", "seed",
                                          sender=lambda *args: sent.append(args[0]))
        self.assertEqual(sent, ["chat-a", "chat-b"])
        self.assertEqual(delivered, ["chat-a", "chat-b"])

    def test_one_failing_group_does_not_stop_the_others_but_still_raises(self):
        sent = []

        def sender(chat_id, *rest):
            if chat_id == "chat-a":
                raise RuntimeError("boom")
            sent.append(chat_id)

        with self.assertRaises(RuntimeError) as caught:
            perf.deliver_to_chats(["chat-a", "chat-b"], "t", "body", "seed", sender=sender)
        self.assertEqual(sent, ["chat-b"])
        self.assertIn("chat-a", str(caught.exception))

    def test_no_destination_is_a_no_op_rather_than_an_error(self):
        self.assertEqual(perf.deliver_to_chats([], "t", "body", "seed", sender=None), [])


class LedgerStateTests(unittest.TestCase):
    def test_merge_keeps_history_when_upstream_window_rolls_off(self):
        older = perf.extract_trades([item("1", "2026-08-31 13:17", name="电广传媒", code="000917",
                                          price=7.45, deal=0, mkt="sz")])
        newer = perf.extract_trades([item("2", "2026-09-03 10:05", name="电广传媒", code="000917",
                                          price=7.35, deal=1, mkt="sz")])
        merged = perf.merge_trades(older, newer)
        self.assertEqual([t["append_id"] for t in merged], ["1", "2"])
        # Re-seeing the same record must not duplicate it.
        self.assertEqual(len(perf.merge_trades(merged, newer)), 2)

    def test_ledger_round_trips_through_the_state_file(self):
        trades = perf.extract_trades([item("1", "2026-09-08 09:47", name="长飞光纤", code="601869",
                                           price=411.0, deal=0)])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "perf.json"
            state = {"ledger": {}, "sent": {}}
            perf.store_ledger(state, "pid", trades)
            perf.save_state(path, state)
            restored = perf.load_state(path)
            self.assertEqual(perf.read_ledger(restored, "pid")[0]["symbol"], "sh601869")

    def test_delivery_key_is_idempotent_per_product_and_period(self):
        state = {"ledger": {}, "sent": {}}
        key = perf.delivery_key("daily", "pid", date(2026, 9, 8))
        self.assertFalse(perf.already_sent(state, key))
        perf.mark_sent(state, key)
        self.assertTrue(perf.already_sent(state, key))
        self.assertNotEqual(key, perf.delivery_key("weekly", "pid", date(2026, 9, 8)))


if __name__ == "__main__":
    unittest.main()
