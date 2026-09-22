"""Top-level one-turn conversation orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stock_common.agent.dispatcher import TaskDispatcher
from stock_common.agent.schemas import (
    AgentResponse,
    AgentStatus,
    SessionState,
    TaskExecutionContext,
    TaskResult,
    TaskSpec,
)
from stock_common.agent.task_parser import HeuristicTaskParser, TaskParser
from stock_common.model_selection import ModelSelectionRequest, ModelSelector
from stock_common.model_selection.schemas import ModelSelection
from stock_common.router import Router
from stock_common.router.schemas import (
    DecisionSource,
    FallbackReason,
    RouteDecision,
    RouteLabel,
)


@dataclass
class FinanceAgent:
    """Coordinate routing, model selection, parsing, and task dispatch.

    This class handles one user message per call. The API, CLI, or frontend owns
    the outer receive loop and passes a SessionState between turns.
    """

    router: Router | None = None
    model_selector: ModelSelector | None = None
    task_parser: TaskParser | None = None
    dispatcher: TaskDispatcher | None = None
    max_tasks: int = 8

    def __post_init__(self) -> None:
        if self.router is None:
            self.router = Router()
        if self.model_selector is None:
            self.model_selector = ModelSelector()
        if self.task_parser is None:
            self.task_parser = HeuristicTaskParser(max_tasks=self.max_tasks)
        if self.dispatcher is None:
            self.dispatcher = TaskDispatcher()

    async def handle_user_message(
        self,
        message: str,
        session: SessionState | None = None,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentResponse:
        """Handle one user turn and update the provided session."""

        session = session or SessionState()
        turn_metadata = dict(metadata or {})
        router_context = session.to_router_context()
        session.record_user_message(message)

        assert self.router is not None
        assert self.model_selector is not None
        assert self.task_parser is not None
        assert self.dispatcher is not None

        route_decision = await self.router.route(message, router_context, request_id=request_id)
        parent_model_selection = self.model_selector.select(
            ModelSelectionRequest(text=message, route_decision=route_decision)
        )

        tasks = await self._build_tasks(
            message=message,
            route_decision=route_decision,
            parent_model_selection=parent_model_selection,
            session=session,
        )
        if len(tasks) > self.max_tasks:
            tasks = tasks[: self.max_tasks]

        task_results = []
        for task in tasks:
            task_model_selection = self._select_model_for_task(
                task=task,
                parent_route_decision=route_decision,
                parent_model_selection=parent_model_selection,
            )
            task_route_decision = self._route_decision_for_task(route_decision, task)
            context_metadata = dict(turn_metadata)
            context_metadata["request_id"] = route_decision.metadata.get("request_id")
            context = TaskExecutionContext(
                session=session,
                route_decision=task_route_decision,
                model_selection=task_model_selection,
                parent_model_selection=parent_model_selection,
                metadata=context_metadata,
            )
            task_results.append(await self.dispatcher.dispatch(task, context))

        response_metadata = dict(turn_metadata)
        response_metadata.update(
            {
                "session_id": session.session_id,
                "request_id": route_decision.metadata.get("request_id"),
            }
        )
        response = AgentResponse(
            content=self._compose_content(tuple(task_results)),
            status=self._aggregate_status(tuple(task_results), len(tasks)),
            route_decision=route_decision,
            model_selection=parent_model_selection,
            tasks=tasks,
            task_results=tuple(task_results),
            metadata=response_metadata,
        )
        session.record_agent_response(response)
        return response

    async def _build_tasks(
        self,
        message: str,
        route_decision: RouteDecision,
        parent_model_selection: ModelSelection,
        session: SessionState,
    ) -> tuple[TaskSpec, ...]:
        assert self.task_parser is not None
        if route_decision.requires_llm_parser:
            return await self.task_parser.parse(
                text=message,
                route_decision=route_decision,
                model_selection=parent_model_selection,
                session=session,
            )

        return (
            TaskSpec(
                task_id="task_001",
                label=route_decision.label,
                text=message,
                metadata={"parser": "direct_route"},
            ),
        )

    def _select_model_for_task(
        self,
        task: TaskSpec,
        parent_route_decision: RouteDecision,
        parent_model_selection: ModelSelection,
    ) -> ModelSelection:
        assert self.model_selector is not None

        if (
            not parent_route_decision.requires_llm_parser
            and task.label is parent_route_decision.label
        ):
            return parent_model_selection

        return self.model_selector.select(
            ModelSelectionRequest(
                text=task.text,
                function=task.label,
                metadata={"parent_route": parent_route_decision.label.value},
            )
        )

    @staticmethod
    def _route_decision_for_task(
        parent_route_decision: RouteDecision,
        task: TaskSpec,
    ) -> RouteDecision:
        if task.label is parent_route_decision.label:
            return parent_route_decision

        return RouteDecision(
            label=task.label,
            confidence=parent_route_decision.confidence,
            source=DecisionSource.DEFAULT,
            requires_llm_parser=task.label in {RouteLabel.MIXED, RouteLabel.UNKNOWN},
            fallback_reason=FallbackReason.NONE,
            metadata={
                "parent_route": parent_route_decision.label.value,
                "parent_request_id": parent_route_decision.metadata.get("request_id"),
            },
        )

    @staticmethod
    def _compose_content(task_results: tuple[TaskResult, ...]) -> str:
        if not task_results:
            return "No task was produced for this request."
        return "\n".join(result.content for result in task_results if result.content)

    @staticmethod
    def _aggregate_status(task_results: tuple[TaskResult, ...], task_count: int) -> AgentStatus:
        if not task_results:
            return AgentStatus.FAILED
        if any(result.status is AgentStatus.FAILED for result in task_results):
            return AgentStatus.FAILED
        if any(result.status is AgentStatus.NEEDS_USER_INPUT for result in task_results):
            return AgentStatus.NEEDS_USER_INPUT
        if len(task_results) < task_count:
            return AgentStatus.PARTIAL
        return AgentStatus.COMPLETED
