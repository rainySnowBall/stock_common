import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_common.agent.schemas import SessionState, TaskExecutionContext, TaskSpec
from stock_common.model_selection import ModelSelectionRequest, ModelSelector
from stock_common.router.schemas import DecisionSource, RouteDecision, RouteLabel
from stock_common.search import SearchAnswer, SearchResult
from stock_common.web.handlers import FinanceChatHandler


class FakeLLMClient:
    def __init__(self) -> None:
        self.calls = []

    async def complete(self, messages):
        self.calls.append(tuple(messages))
        return "llm answer"


class FakeSearchSynthesizer:
    def __init__(self) -> None:
        self.calls = []

    async def answer(self, query, history=()):
        self.calls.append({"query": query, "history": tuple(history)})
        return SearchAnswer(
            query=query,
            content="search answer",
            results=(SearchResult(title="Source", url="https://example.com", snippet="result"),),
        )


class FinanceChatHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_chat_uses_llm_without_search(self) -> None:
        llm = FakeLLMClient()
        search = FakeSearchSynthesizer()
        handler = FinanceChatHandler(llm_client=llm, search_synthesizer=search)

        result = await handler.handle(
            TaskSpec(task_id="task_001", label=RouteLabel.CHAT, text="PE?"),
            _context(metadata={}),
        )

        self.assertEqual(result.content, "llm answer")
        self.assertEqual(result.metadata["handler"], "llm_chat")
        self.assertFalse(result.metadata["web_search"])
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(search.calls, [])

    async def test_web_search_param_uses_search_synthesizer(self) -> None:
        llm = FakeLLMClient()
        search = FakeSearchSynthesizer()
        handler = FinanceChatHandler(llm_client=llm, search_synthesizer=search)

        result = await handler.handle(
            TaskSpec(task_id="task_001", label=RouteLabel.CHAT, text="PE?"),
            _context(metadata={"web_search": True}),
        )

        self.assertEqual(result.content, "search answer")
        self.assertEqual(result.metadata["handler"], "search_grounded_chat")
        self.assertTrue(result.metadata["web_search"])
        self.assertEqual(llm.calls, [])
        self.assertEqual(search.calls[0]["query"], "PE?")


def _context(metadata):
    route = RouteDecision(
        label=RouteLabel.CHAT,
        confidence=0.95,
        source=DecisionSource.RULE,
        requires_llm_parser=False,
    )
    selection = ModelSelector().select(
        ModelSelectionRequest(text="PE?", route_decision=route)
    )
    return TaskExecutionContext(
        session=SessionState(session_id="session_test"),
        route_decision=route,
        model_selection=selection,
        metadata=metadata,
    )


if __name__ == "__main__":
    unittest.main()
