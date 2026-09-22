"""Zero-dependency local web server for router and agent interaction."""

from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from stock_common.agent import (
    FactorResearchTaskHandler,
    FinanceAgent,
    SessionState,
    TaskDispatcher,
)
from stock_common.backtest import FactorResearchPipeline
from stock_common.backtest.compiler import HeuristicStrategyParser
from stock_common.backtest.llm_extract import LLMExtractStrategyParser
from stock_common.backtest.real_engine import SQLiteFactorBacktestEngine
from stock_common.backtest.stock_resolver import StockSymbolResolver
from stock_common.router.schemas import RouteLabel
from stock_common.web.handlers import FinanceChatHandler, RoutingPreviewHandler


STATIC_DIR = Path(__file__).resolve().parent / "static"
ROOT_DIR = Path(__file__).resolve().parents[3]
REPORTS_DIR = ROOT_DIR / "output" / "pdf"


class SessionStore:
    """In-memory session store for local development."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    def get(self, session_id: str | None) -> SessionState:
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]

        session = SessionState(session_id=session_id) if session_id else SessionState()
        self._sessions[session.session_id] = session
        return session

    def reset(self, session_id: str | None) -> SessionState:
        session = SessionState(session_id=session_id) if session_id else SessionState()
        self._sessions[session.session_id] = session
        return session

    def count(self) -> int:
        return len(self._sessions)


def build_agent() -> FinanceAgent:
    """Build the web agent with decoupled handlers."""

    stock_resolver = StockSymbolResolver(
        enable_llm_extract=True,
    )
    factor_parser = LLMExtractStrategyParser(
        fallback_parser=HeuristicStrategyParser(stock_resolver=stock_resolver),
        stock_resolver=stock_resolver,
    )
    factor_pipeline = FactorResearchPipeline(
        parser=factor_parser,
        engine=SQLiteFactorBacktestEngine(use_local_daily_basic_factors=False),
    )
    return FinanceAgent(
        dispatcher=TaskDispatcher(
            handlers={
                RouteLabel.CHAT: FinanceChatHandler(),
                RouteLabel.FACTOR_RESEARCH: FactorResearchTaskHandler(
                    pipeline=factor_pipeline
                ),
                RouteLabel.MIXED: RoutingPreviewHandler(RouteLabel.MIXED),
                RouteLabel.UNKNOWN: RoutingPreviewHandler(RouteLabel.UNKNOWN),
            }
        )
    )


class WebAppState:
    """Mutable state shared by request handlers."""

    def __init__(self) -> None:
        self.agent = build_agent()
        self.sessions = SessionStore()


class FinanceRouterRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the local web UI."""

    server_version = "FinanceRouterWeb/0.1"
    app_state: WebAppState

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._send_json(
                {
                    "status": "ok",
                    "sessions": self.app_state.sessions.count(),
                }
            )
            return

        if parsed.path.startswith("/reports/"):
            self._serve_report(parsed.path)
            return

        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/chat":
            self._handle_chat()
            return

        if parsed.path == "/api/session/reset":
            self._handle_reset()
            return

        self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        del format, args

    def _handle_chat(self) -> None:
        payload = self._read_json()
        message = str(payload.get("message", "")).strip()
        session_id = _optional_str(payload.get("session_id"))
        web_search = _optional_bool(
            payload.get("web_search", payload.get("use_web_search", False))
        )

        if not message:
            self._send_json(
                {"error": "message_required"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        session = self.app_state.sessions.get(session_id)
        response = asyncio.run(
            self.app_state.agent.handle_user_message(
                message=message,
                session=session,
                metadata={"web_search": web_search},
            )
        )

        self._send_json(
            {
                "session_id": session.session_id,
                "message": response.content,
                "response": response.to_dict(),
                "session": {
                    "session_id": session.session_id,
                    "previous_intent": (
                        session.previous_intent.value
                        if hasattr(session.previous_intent, "value")
                        else session.previous_intent
                    ),
                    "conversation_summary": session.conversation_summary,
                    "message_count": len(session.messages),
                },
            }
        )

    def _handle_reset(self) -> None:
        payload = self._read_json()
        session_id = _optional_str(payload.get("session_id"))
        session = self.app_state.sessions.reset(session_id)
        self._send_json(
            {
                "session_id": session.session_id,
                "session": {
                    "session_id": session.session_id,
                    "message_count": len(session.messages),
                },
            }
        )

    def _serve_static(self, path: str) -> None:
        if path in {"", "/"}:
            relative_path = Path("index.html")
        else:
            relative_path = Path(path.lstrip("/"))

        static_path = (STATIC_DIR / relative_path).resolve()
        try:
            static_path.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self._send_json({"error": "invalid_path"}, status=HTTPStatus.BAD_REQUEST)
            return

        if not static_path.exists() or not static_path.is_file():
            static_path = STATIC_DIR / "index.html"

        content_type = mimetypes.guess_type(static_path.name)[0] or "application/octet-stream"
        data = static_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_report(self, path: str) -> None:
        raw_relative = unquote(path.removeprefix("/reports/")).strip()
        if not raw_relative:
            self._send_json({"error": "report_not_found"}, status=HTTPStatus.NOT_FOUND)
            return

        relative_path = Path(raw_relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            self._send_json({"error": "invalid_path"}, status=HTTPStatus.BAD_REQUEST)
            return

        report_path = (REPORTS_DIR / relative_path).resolve()
        try:
            report_path.relative_to(REPORTS_DIR.resolve())
        except ValueError:
            self._send_json({"error": "invalid_path"}, status=HTTPStatus.BAD_REQUEST)
            return

        if (
            not report_path.exists()
            or not report_path.is_file()
            or report_path.suffix.lower() != ".pdf"
        ):
            self._send_json({"error": "report_not_found"}, status=HTTPStatus.NOT_FOUND)
            return

        data = report_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", f'inline; filename="{report_path.name}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            return {}

        raw_body = self.rfile.read(content_length)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

        if not isinstance(payload, dict):
            return {}
        return payload

    def _send_json(
        self,
        payload: dict[str, Any],
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def create_server(host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    app_state = WebAppState()

    class BoundHandler(FinanceRouterRequestHandler):
        pass

    BoundHandler.app_state = app_state
    return ThreadingHTTPServer((host, port), BoundHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local finance research web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    server = create_server(host=args.host, port=args.port)
    host, port = server.server_address
    print(f"Finance Research Web UI running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


if __name__ == "__main__":
    main()
