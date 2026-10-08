import unittest
from unittest.mock import patch

from apis.xhs_pc_apis import XHS_Apis
from apis.xhs_pugongying_apis import PuGongYingAPI
from apis.xhs_qianfan_apis import QianFanAPI


class Response:
    def __init__(self, payload=None, status_code=200, text=''):
        self.payload = payload
        self.status_code = status_code
        self.text = text
        self.cookies = {}

    def json(self):
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


class Http:
    def __init__(self, response):
        self.response = response

    def post(self, *args, **kwargs):
        return self.response


class ApiCompatibilityTests(unittest.TestCase):
    def test_onebox_falls_back_to_live_recommendations(self):
        api = object.__new__(XHS_Apis)
        api.base_url = 'https://edith.example'
        api.http = Http(Response({'success': False, 'code': 0, 'msg': '成功', 'data': {}}))
        api._request_params = lambda *args, **kwargs: ({}, {}, '{}')
        api._merge_response_cookies = lambda *args, **kwargs: None
        api._proxies = lambda proxies=None: proxies
        api.get_search_keyword = lambda query, proxies=None: (
            True, '成功', {'data': {'sug_items': [{'text': query + ' infra'}]}}
        )

        result = api.search_onebox('AI')

        self.assertTrue(result['success'])
        self.assertEqual(result['compat_fallback'], 'search/recommend')
        self.assertEqual(result['data']['sug_items'][0]['text'], 'AI infra')

    def test_pgy_category_failure_is_explicit(self):
        api = object.__new__(PuGongYingAPI)
        api.base_url = 'https://pgy.example'
        api._signed_headers = lambda cookies, operation: {}
        response = Response(status_code=401)
        with patch('apis.xhs_pugongying_apis.requests.get', return_value=response):
            with self.assertRaisesRegex(RuntimeError, r'valid Pugongying session.*HTTP 401'):
                api.get_all_categories({})

    def test_qianfan_category_failure_is_explicit(self):
        api = QianFanAPI()
        response = Response({'code': -100, 'success': False, 'msg': '无登录信息', 'data': {}}, status_code=401)
        with patch('apis.xhs_qianfan_apis.requests.get', return_value=response):
            with self.assertRaisesRegex(RuntimeError, r'valid Qianfan session.*HTTP 401'):
                api.get_all_categories({})


if __name__ == '__main__':
    unittest.main()
