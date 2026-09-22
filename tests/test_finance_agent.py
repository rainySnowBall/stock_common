import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_common.agent import (
    AgentStatus,
    FactorResearchTaskHandler,
    FinanceAgent,
    SessionState,
    TaskDispatcher,
    TaskResult,
)
from stock_common.router.schemas import RouteLabel


class RecordingHandler:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = []

    async def handle(self, task, context):
        self.calls.append(
            {
                "task": task,
                "model_id": context.model_selection.model.model_id,
                "route_label": context.route_decision.label,
            }
        )
        return TaskResult(
            task=task,
            content=f"{self.name}:{task.text}",
            status=AgentStatus.COMPLETED,
            model_selection=context.model_selection,
        )


class FailingHandler:
    async def handle(self, task, context):
        del task, context
        raise RuntimeError("downstream unavailable")


class FinanceAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_chat_turn_dispatches_to_chat_handler(self) -> None:
        chat_handler = RecordingHandler("chat")
        session = SessionState(session_id="session_test")
        agent = FinanceAgent(
            dispatcher=TaskDispatcher(handlers={RouteLabel.CHAT: chat_handler})
        )

        response = await agent.handle_user_message("ROE 是什么？", session=session)

        self.assertEqual(response.status, AgentStatus.COMPLETED)
        self.assertEqual(response.route_decision.label, RouteLabel.CHAT)
        self.assertEqual(response.tasks[0].label, RouteLabel.CHAT)
        self.assertEqual(response.model_selection.model.model_id, "finance-chat-fast")
        self.assertEqual(chat_handler.calls[0]["model_id"], "finance-chat-fast")
        self.assertEqual(session.previous_intent, RouteLabel.CHAT)
        self.assertEqual(len(session.messages), 2)

    async def test_mixed_turn_parses_and_dispatches_subtasks(self) -> None:
        chat_handler = RecordingHandler("chat")
        factor_handler = RecordingHandler("factor")
        agent = FinanceAgent(
            dispatcher=TaskDispatcher(
                handlers={
                    RouteLabel.CHAT: chat_handler,
                    RouteLabel.FACTOR_RESEARCH: factor_handler,
                }
            )
        )

        response = await agent.handle_user_message("先查 PE，再回测低 PE 策略")

        self.assertEqual(response.status, AgentStatus.COMPLETED)
        self.assertEqual(response.route_decision.label, RouteLabel.MIXED)
        self.assertTrue(response.route_decision.requires_llm_parser)
        self.assertEqual(response.model_selection.model.model_id, "finance-research-strong")
        self.assertEqual([task.label for task in response.tasks], [RouteLabel.CHAT, RouteLabel.FACTOR_RESEARCH])
        self.assertEqual(chat_handler.calls[0]["model_id"], "finance-chat-fast")
        self.assertEqual(factor_handler.calls[0]["model_id"], "finance-research-balanced")
        self.assertIn("chat:查 PE", response.content)
        self.assertIn("factor:回测低 PE 策略", response.content)

    async def test_unknown_turn_asks_for_more_input_with_default_handler(self) -> None:
        response = await FinanceAgent().handle_user_message("写一首诗")

        self.assertEqual(response.status, AgentStatus.NEEDS_USER_INPUT)
        self.assertEqual(response.tasks[0].label, RouteLabel.UNKNOWN)
        self.assertTrue(response.tasks[0].requires_user_input)

    def test_default_dispatcher_uses_factor_research_pipeline_handler(self) -> None:
        dispatcher = TaskDispatcher()

        self.assertIsInstance(
            dispatcher.handlers[RouteLabel.FACTOR_RESEARCH],
            FactorResearchTaskHandler,
        )

    async def test_handler_exception_returns_failed_response(self) -> None:
        agent = FinanceAgent(
            dispatcher=TaskDispatcher(handlers={RouteLabel.CHAT: FailingHandler()})
        )

        response = await agent.handle_user_message("MACD 是什么？")

        self.assertEqual(response.status, AgentStatus.FAILED)
        self.assertEqual(response.task_results[0].status, AgentStatus.FAILED)
        self.assertIn("error_type", response.task_results[0].metadata)

    async def test_dispatcher_accepts_string_handler_keys(self) -> None:
        chat_handler = RecordingHandler("chat")
        agent = FinanceAgent(
            dispatcher=TaskDispatcher(handlers={"chat": chat_handler})
        )

        response = await agent.handle_user_message("PE 是什么？")

        self.assertEqual(response.status, AgentStatus.COMPLETED)
        self.assertEqual(chat_handler.calls[0]["route_label"], RouteLabel.CHAT)


if __name__ == "__main__":
    unittest.main()
