import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_common.search import (
    SearchAnswerSynthesizer,
    SearchHistoryMessage,
    SearchResult,
    WebSearchClient,
    WebSearchSettings,
)
from stock_common.search.schemas import build_web_search_api_url
from stock_common.search.web_search import extract_search_results


class FakeSearchHandler(BaseHTTPRequestHandler):
    captured_payload = None
    captured_authorization = None

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length).decode("utf-8")
        FakeSearchHandler.captured_payload = json.loads(body)
        FakeSearchHandler.captured_authorization = self.headers.get("Authorization")

        payload = {
            "data": {
                "results": [
                    {
                        "title": "结果一",
                        "url": "https://example.com/a",
                        "snippet": "宁德时代毛利率受到电池材料价格和产品结构影响。",
                        "source": "Example A",
                    },
                    {
                        "title": "结果二",
                        "url": "https://example.com/b",
                        "summary": "公司财报数据显示新能源电池业务收入占比较高。",
                        "source": "Example B",
                    },
                ]
            }
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        del format, args


class SearchTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeSearchHandler.captured_payload = None
        FakeSearchHandler.captured_authorization = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeSearchHandler)
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}/search"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_settings_from_env(self) -> None:
        settings = WebSearchSettings.from_env(
            env={
                "FINANCE_WEB_SEARCH_API_URL": "https://search.example/api",
                "FINANCE_WEB_SEARCH_API_KEY": "dummy-key",
                "FINANCE_WEB_SEARCH_WORKSPACE": "default",
                "FINANCE_WEB_SEARCH_SERVICE_ID": "ops-web-search-001",
                "FINANCE_WEB_SEARCH_QUERY_REWRITE": "false",
                "FINANCE_WEB_SEARCH_TOP_K": "3",
                "FINANCE_WEB_SEARCH_CONTENT_TYPE": "summary",
                "FINANCE_WEB_SEARCH_WAY": "lite",
                "FINANCE_WEB_SEARCH_TIMEOUT_SECONDS": "8",
                "FINANCE_WEB_SEARCH_MAX_RETRIES": "0",
            },
            load_env_files=False,
        )

        self.assertEqual(
            settings.api_url,
            "https://search.example/api/v3/openapi/workspaces/default/web-search/ops-web-search-001",
        )
        self.assertEqual(settings.api_key, "dummy-key")
        self.assertFalse(settings.query_rewrite)
        self.assertEqual(settings.top_k, 3)
        self.assertEqual(settings.content_type.value, "summary")
        self.assertEqual(settings.way.value, "lite")
        self.assertEqual(settings.timeout_seconds, 8)
        self.assertEqual(settings.max_retries, 0)

    def test_settings_from_env_supports_online_search_aliases(self) -> None:
        settings = WebSearchSettings.from_env(
            env={
                "ONLINE_SEARCH_BASE_URL": "https://search.example",
                "ONLINE_SEARCH_API_KEY": "alias-key",
            },
            load_env_files=False,
        )

        self.assertEqual(
            settings.api_url,
            "https://search.example/v3/openapi/workspaces/default/web-search/ops-web-search-001",
        )
        self.assertEqual(settings.api_key, "alias-key")

    def test_build_web_search_api_url_preserves_full_endpoint(self) -> None:
        full_url = (
            "https://search.example/v3/openapi/workspaces/default/"
            "web-search/ops-web-search-001"
        )

        self.assertEqual(
            build_web_search_api_url({"FINANCE_WEB_SEARCH_API_URL": full_url}),
            full_url,
        )

    def test_build_web_search_api_url_supports_quoted_base_url(self) -> None:
        self.assertEqual(
            build_web_search_api_url(
                {
                    "FINANCE_WEB_SEARCH_API_URL": '"https://search.example"',
                    "FINANCE_WEB_SEARCH_WORKSPACE": "finance",
                    "FINANCE_WEB_SEARCH_SERVICE_ID": "search-001",
                }
            ),
            "https://search.example/v3/openapi/workspaces/finance/web-search/search-001",
        )

    def test_client_posts_expected_payload_and_parses_results(self) -> None:
        client = WebSearchClient(
            WebSearchSettings(
                api_url=self.base_url,
                api_key="test-key",
                query_rewrite=True,
                top_k=5,
                content_type="snippet",
                way="pro",
                timeout_seconds=5,
                max_retries=0,
            )
        )

        results = client.search_sync(
            query="宁德时代毛利率",
            history=(SearchHistoryMessage(role="user", content="之前的问题"),),
        )

        self.assertEqual(FakeSearchHandler.captured_authorization, "Bearer test-key")
        self.assertEqual(FakeSearchHandler.captured_payload["query"], "宁德时代毛利率")
        self.assertTrue(FakeSearchHandler.captured_payload["query_rewrite"])
        self.assertEqual(FakeSearchHandler.captured_payload["top_k"], 5)
        self.assertEqual(FakeSearchHandler.captured_payload["content_type"], "snippet")
        self.assertEqual(FakeSearchHandler.captured_payload["way"], "pro")
        self.assertEqual(FakeSearchHandler.captured_payload["history"][0]["role"], "user")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].title, "结果一")
        self.assertEqual(results[1].summary, "公司财报数据显示新能源电池业务收入占比较高。")

    def test_extract_search_results_handles_nested_payloads(self) -> None:
        payload = {
            "outer": {
                "records": [
                    {
                        "name": "来源",
                        "link": "https://example.com",
                        "mainText": "正文内容",
                    }
                ]
            }
        }

        results = extract_search_results(payload, limit=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "来源")
        self.assertEqual(results[0].main_text, "正文内容")

    def test_synthesizer_summarizes_multiple_results(self) -> None:
        answer = SearchAnswerSynthesizer(client=None).synthesize(
            query="贵州茅台为什么下跌",
            results=(
                SearchResult(title="A", url="https://a.example", snippet="市场风险偏好下降。"),
                SearchResult(title="B", url="https://b.example", summary="白酒板块整体调整。"),
            ),
        )

        self.assertIn("贵州茅台为什么下跌", answer)
        self.assertIn("市场风险偏好下降", answer)
        self.assertIn("白酒板块整体调整", answer)
        self.assertIn("https://a.example", answer)


if __name__ == "__main__":
    unittest.main()
