import unittest
from datetime import date
from unittest.mock import patch

from app.longhu_settled_quotes import fetch_classified, normalized_quote


class NormalizedQuoteTests(unittest.TestCase):
    def test_uses_requested_historical_day_not_latest_live_day(self):
        rows={'x':['20260908','20260909','20260910'], 'y':[[8,7.75,8.08,7.51],[7.5,7.63,8.03,7.46],[8,8,9,7]],'vol':[100,200,300],'bal':[1000,2000,3000]}
        q=normalized_quote('600664.SH',date(2026,9,9),rows)
        assert q['close']==7.63 and q['pre_close']==7.75 and q['amount']==2000
        assert q['trade_date']=='20260909'

    def test_missing_day_or_bad_candle_is_not_synthesized(self):
        assert normalized_quote('600664.SH',date(2026,9,9),{'x':['20260908'],'y':[[8,8,8,8]]}) is None
        assert normalized_quote('600664.SH',date(2026,9,9),{'x':['20260908','20260909'],'y':[[8,8,8,8],[8,9,7,6]]}) is None

    def test_classifies_cq_and_missing_bar_evidence(self):
        day = date(2026, 9, 9)
        payload = {'x': ['20260908', '20260909', '20260910'],
                   'y': [[8, 7.75, 8.08, 7.51], [7.5, 7.63, 8.03, 7.46], [8, 8, 9, 7]],
                   'vol': [100, 200, 300], 'bal': [1000, 2000, 3000],
                   'CQ': ['', '', '']}

        class Source:
            def raw_call(self, request):
                symbol = request['params']['StockID']
                if symbol == '000002':
                    return {'pages': [{'payload': {**payload, 'CQ': ['', '', '0,0,0,2.49']}}]}
                if symbol == '000003':
                    return {'pages': [{'payload': {k: v for k, v in payload.items() if k != 'CQ'}}]}
                if symbol == '000004':
                    return {'pages': [{'payload': {'x': ['20260908'], 'y': [[8, 8, 8, 8]], 'CQ': ['']}}]}
                return {'pages': [{'payload': payload}]}

        quotes, health = fetch_classified(Source(), ['000001.SZ', '000002.SZ', '000003.SZ', '000004.SZ'], day)
        self.assertEqual([q['ts_code'] for q in quotes], ['000001.SZ'])
        self.assertEqual(health['outcomes']['adjusted_later'], ['000002.SZ'])
        self.assertEqual(health['outcomes']['unverified'], ['000003.SZ'])
        self.assertEqual(health['outcomes']['not_traded'], ['000004.SZ'])

    def test_invalid_bar_is_classified(self):
        class Source:
            def raw_call(self, _request):
                return {'pages': [{'payload': {'x': ['20260908', '20260909'],
                                                'y': [[8, 8, 8, 8], [8, 9, 7, 6]],
                                                'CQ': ['', '']}}]}
        _quotes, health = fetch_classified(Source(), ['000001.SZ'], date(2026, 9, 9))
        self.assertEqual(health['outcomes']['invalid'], ['000001.SZ'])

    def test_failed_request_keeps_error_text(self):
        class Source:
            def raw_call(self, _request):
                raise TimeoutError('gateway timed out')
        _quotes, health = fetch_classified(Source(), ['000001.SZ'], date(2026, 9, 9))
        self.assertEqual(health['outcomes']['failed'], ['000001.SZ'])
        self.assertEqual(health['errors']['000001.SZ'], 'TimeoutError: gateway timed out')

    def test_short_cq_is_unverified(self):
        class Source:
            def raw_call(self, _request):
                return {'pages': [{'payload': {'x': ['20260908', '20260909'],
                                                'y': [[8, 8, 8, 8], [8, 9, 10, 7]],
                                                'CQ': ['']}}]}
        _quotes, health = fetch_classified(Source(), ['000001.SZ'], date(2026, 9, 9))
        self.assertEqual(health['outcomes']['unverified'], ['000001.SZ'])

    def test_marker_on_day_is_filled_and_ex_rights_is_reported(self):
        class Source:
            def raw_call(self, _request):
                return {'pages': [{'payload': {'x': ['20260908', '20260909'],
                                                'y': [[8, 8, 8, 8], [8, 9, 10, 8]],
                                                'CQ': ['', '0,0,0,2.49']}}]}
        quotes, health = fetch_classified(Source(), ['000001.SZ'], date(2026, 9, 9))
        self.assertEqual(health['outcomes']['filled'], ['000001.SZ'])
        self.assertTrue(health['ex_rights_on_day']['000001.SZ'])
        self.assertTrue(quotes[0]['ex_rights_on_day'])

    def test_deadline_stops_new_requests_and_marks_unreached_symbols(self):
        calls = []
        payload = {'x': ['20260908', '20260909'], 'y': [[8, 8, 8, 8], [8, 9, 10, 8]], 'CQ': ['', '']}

        class Source:
            def raw_call(self, request):
                calls.append(request['params']['StockID'])
                return {'pages': [{'payload': payload}]}

        with patch('app.longhu_settled_quotes.monotonic', side_effect=[0, 0, 0, 1]):
            quotes, health = fetch_classified(
                Source(), ['000001.SZ', '000002.SZ'], date(2026, 9, 9), workers=1, deadline_seconds=0.5,
            )
        self.assertEqual(calls, ['000001'])
        self.assertEqual([quote['ts_code'] for quote in quotes], ['000001.SZ'])
        self.assertEqual(health['errors']['000002.SZ'], 'deadline')


if __name__ == "__main__":
    unittest.main()
