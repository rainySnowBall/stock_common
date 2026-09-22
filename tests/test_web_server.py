import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_common.agent.handlers import FactorResearchTaskHandler
from stock_common.backtest.real_engine import SQLiteFactorBacktestEngine
from stock_common.router.schemas import RouteLabel
from stock_common.web.server import create_server


class WebServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patcher = patch.dict(
            os.environ,
            {
                "FINANCE_CHAT_FAST_MODEL_NAME": "unit-test-chat",
                "FINANCE_CHAT_FAST_BASE_URL": "http://127.0.0.1:9/v1",
                "FINANCE_CHAT_FAST_API_KEY": "unit-test-key",
                "FINANCE_CHAT_FAST_TIMEOUT_SECONDS": "0.1",
                "FINANCE_CHAT_FAST_MAX_RETRIES": "0",
            },
        )
        self.env_patcher.start()
        self.server = create_server(port=0)
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.env_patcher.stop()

    def test_index_page_is_served(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/", timeout=5) as response:
            body = response.read().decode("utf-8")

        self.assertEqual(response.status, 200)
        self.assertIn("Finance Research", body)
        self.assertIn("/app.js", body)
        self.assertNotIn("lastModel", body)
        self.assertNotIn("lastRoute", body)
        self.assertNotIn("topbar-metrics", body)

    def test_frontend_does_not_render_model_selection(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/app.js", timeout=5) as response:
            body = response.read().decode("utf-8")

        self.assertEqual(response.status, 200)
        self.assertNotIn("lastModel", body)
        self.assertNotIn('renderMetaCard("模型"', body)
        self.assertNotIn("renderMetadata", body)
        self.assertNotIn("renderMetaCard", body)

    def test_frontend_renders_assistant_markdown(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/app.js", timeout=5) as response:
            body = response.read().decode("utf-8")

        self.assertEqual(response.status, 200)
        self.assertIn("renderMarkdown(content)", body)
        self.assertIn('contentEl.classList.add("markdown-content")', body)
        self.assertIn("renderLatexMath", body)
        self.assertIn("math-frac", body)

    def test_frontend_renders_backtest_chart_artifacts(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/app.js", timeout=5) as response:
            body = response.read().decode("utf-8")

        self.assertEqual(response.status, 200)
        self.assertIn("renderBacktestArtifacts(response)", body)
        self.assertIn("nav_curve", body)
        self.assertIn("monthly_return_heatmap", body)
        self.assertIn("svg-chart", body)
        self.assertIn("renderReportLink", body)
        self.assertIn("下载 PDF 报告", body)

    def test_report_route_serves_generated_pdf_files(self) -> None:
        report_dir = Path(__file__).resolve().parents[1] / "output" / "pdf"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "unit-test-report.pdf"
        report_path.write_bytes(b"%PDF-1.4\n% unit test\n")
        try:
            with urllib.request.urlopen(
                f"{self.base_url}/reports/unit-test-report.pdf",
                timeout=5,
            ) as response:
                body = response.read()

            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "application/pdf")
            self.assertTrue(body.startswith(b"%PDF"))
        finally:
            report_path.unlink(missing_ok=True)

    def test_web_agent_uses_real_factor_backtest_engine(self) -> None:
        agent = self.server.RequestHandlerClass.app_state.agent
        assert agent.dispatcher is not None
        handler = agent.dispatcher.handlers[RouteLabel.FACTOR_RESEARCH]

        self.assertIsInstance(handler, FactorResearchTaskHandler)
        self.assertIsInstance(handler.pipeline.engine, SQLiteFactorBacktestEngine)

    def test_chat_api_returns_route_and_model_selection(self) -> None:
        payload = {"message": "先查 PE，再回测低 PE 策略"}
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertIn("session_id", body)
        self.assertEqual(body["response"]["route_decision"]["label"], "mixed")
        self.assertEqual(body["response"]["model_selection"]["model_id"], "finance-research-strong")
        self.assertEqual(
            [task["label"] for task in body["response"]["tasks"]],
            ["chat", "factor_research"],
        )

    def test_chat_api_rejects_empty_message(self) -> None:
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps({"message": ""}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=5)

        self.assertEqual(error.exception.code, 400)
        error.exception.close()


if __name__ == "__main__":
    unittest.main()
