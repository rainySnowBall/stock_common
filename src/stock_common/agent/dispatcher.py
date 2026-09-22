"""Dispatch parsed tasks to decoupled task handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from stock_common.agent.handlers import (
    FactorResearchTaskHandler,
    PlaceholderTaskHandler,
    TaskHandler,
)
from stock_common.agent.schemas import AgentStatus, TaskExecutionContext, TaskResult, TaskSpec
from stock_common.router.schemas import RouteLabel, coerce_label


@dataclass
class TaskDispatcher:
    """Dispatch tasks by RouteLabel without knowing business internals."""

    handlers: Mapping[RouteLabel, TaskHandler] | None = None
    default_handler: TaskHandler = field(
        default_factory=lambda: PlaceholderTaskHandler(RouteLabel.UNKNOWN)
    )

    def __post_init__(self) -> None:
        if self.handlers is None:
            self.handlers = {
                RouteLabel.CHAT: PlaceholderTaskHandler(RouteLabel.CHAT),
                RouteLabel.FACTOR_RESEARCH: FactorResearchTaskHandler(),
                RouteLabel.UNKNOWN: self.default_handler,
            }
        else:
            self.handlers = {
                coerce_label(label): handler for label, handler in self.handlers.items()
            }

    async def dispatch(self, task: TaskSpec, context: TaskExecutionContext) -> TaskResult:
        assert self.handlers is not None
        handler = self.handlers.get(task.label, self.default_handler)
        try:
            return await handler.handle(task, context)
        except Exception as exc:
            return TaskResult(
                task=task,
                content=f"{task.label.value} handler failed.",
                status=AgentStatus.FAILED,
                model_selection=context.model_selection,
                metadata={"error_type": type(exc).__name__, "error": str(exc)},
            )
