import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wechat_article_enricher import _account_match, _search_candidates, _title_match, _ArticleParser, _captcha


class _FakeEnricher:
    def enrich(self, account, title, summary, original_url):
        from wechat_article_enricher import ArticleResult, STATUS_FULLTEXT
        return ArticleResult(STATUS_FULLTEXT, text="标题：盘面无主线\n正文：完整正文", title=title, article_url="https://mp.weixin.qq.com/s/signed")


class ArticleEnricherTests(unittest.TestCase):
    def test_search_candidates_and_matching(self):
        html = '''<li id="sogou_vr_11002601_box_0"><h3><a id="sogou_vr_11002601_title_0" href="/link?url=x">盘面无主线</a></h3><p class="txt-info">摘要</p><span class="all-time-y2">安强投资记</span></li>'''
        row = list(_search_candidates(html))[0]
        self.assertEqual(row["title"], "盘面无主线")
        self.assertEqual(row["account"], "安强投资记")
        self.assertTrue(_title_match("盘面无主线", row["title"]))
        self.assertTrue(_account_match("安强投资记", row["account"]))

    def test_article_dom_extraction(self):
        parser = _ArticleParser()
        parser.feed('<h1 id="activity-name">盘面无主线</h1><span id="js_name">马安强</span><div id="js_content"><p>第一段</p><script>window.noise = "不要进入正文";</script><p>第二段</p></div>')
        self.assertEqual("盘面无主线", " ".join(parser.title_parts).strip())
        self.assertEqual("马安强", " ".join(parser.author_parts).strip())
        self.assertIn("第一段", " ".join(parser._parts))
        self.assertNotIn("不要进入正文", " ".join(parser._parts))

    def test_relay_article_uses_fulltext_result(self):
        import importlib.util
        relay_path = Path(__file__).resolve().parents[1] / "wechat-biz-relay.py"
        spec = importlib.util.spec_from_file_location("wechat_biz_relay", relay_path)
        relay = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(relay)
        item = {"message_type": "49", "article_account": "安强投资记", "article_title": "盘面无主线", "article_summary": "摘要", "article_url": "http://mp.weixin.qq.com/s/x", "text": "摘要"}
        result = relay.enrich_public_article(item, enricher=_FakeEnricher())
        self.assertEqual(result["article_enrichment_status"], "fulltext_ready")
        self.assertIn("完整正文", result["text"])

    def test_verification_page_is_not_article(self):
        self.assertTrue(_captcha("环境异常 当前环境异常，完成验证后即可继续访问。", "https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha"))

    def test_unregistered_account_is_not_enriched(self):
        import importlib.util
        relay_path = Path(__file__).resolve().parents[1] / "wechat-biz-relay.py"
        spec = importlib.util.spec_from_file_location("wechat_biz_relay_allowlist", relay_path)
        relay = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(relay)
        item = {"_username": "gh_other", "message_type": "49", "article_title": "标题", "text": "摘要"}
        result = relay.enrich_public_article(item, enricher=_FakeEnricher(), allowed_accounts={"gh_926c397be7d3"})
        self.assertNotIn("article_enrichment_status", result)

    def test_public_account_route_defaults_to_hold(self):
        import importlib.util
        relay_path = Path(__file__).resolve().parents[1] / "wechat-biz-relay.py"
        spec = importlib.util.spec_from_file_location("wechat_biz_relay_routes", relay_path)
        relay = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(relay)
        config = relay.load_route_config(Path("/nonexistent/wechat-biz-routes.json"))
        self.assertEqual(config["default"]["kind"], "hold")
        self.assertEqual(relay.account_sinks("gh_unregistered", config)[0]["kind"], "hold")

    def test_route_sink_normalization_supports_webhook_and_email(self):
        import importlib.util
        relay_path = Path(__file__).resolve().parents[1] / "wechat-biz-relay.py"
        spec = importlib.util.spec_from_file_location("wechat_biz_relay_sink_routes", relay_path)
        relay = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(relay)
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "route-test.json"
            config_path.write_text('{"version":1,"default":{"kind":"hold"},"accounts":{"gh_web":{"sinks":[{"kind":"webhook","url":"https://example.invalid/hook"}]},"gh_mail":{"sinks":[{"kind":"email","to":["test@example.com"]}]}}}', encoding="utf-8")
            config = relay.load_route_config(config_path)
            self.assertEqual(relay.account_sinks("gh_web", config)[0]["kind"], "webhook")
            self.assertEqual(relay.account_sinks("gh_mail", config)[0]["to"], ["test@example.com"])

    def test_direct_article_url_fallback_recovers_fulltext(self):
        from wechat_article_enricher import SogouArticleEnricher, STATUS_FULLTEXT

        class DirectOnlyEnricher(SogouArticleEnricher):
            def _get(self, url, referer="", max_bytes=5 * 1024 * 1024):
                if "sogou" in url:
                    return url, "<html>no matching result</html>"
                return url, '<h1 id="activity-name">标题</h1><span id="js_name">作者</span><div id="js_content"><p>这是足够长的正文内容，用于验证公众号原文链接回退路径可以提取全文，而不是只保留摘要。这里补充更多文字以满足正文完整性校验，并确保测试覆盖直接原文抓取。继续补充行情背景和风险提示内容。</p></div>'

        result = DirectOnlyEnricher(timeout=2).enrich("账号", "标题", "摘要", "https://mp.weixin.qq.com/s/example")
        self.assertEqual(result.status, STATUS_FULLTEXT)
        self.assertIn("正文：", result.text)


if __name__ == "__main__":
    unittest.main()
