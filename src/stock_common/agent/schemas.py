"""Data contracts for the conversation orchestration layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from stock_common.model_selection.schemas import ModelSelection
from stock_common.router.schemas import RouteDecision, RouteLabel, RouterContext, coerce_label


class MessageRole(str, Enum):
    """Roles used in a session transcript."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class AgentStatus(str, Enum):
    """High-level outcome for an agent turn or subtask."""

    COMPLETED = "completed"
    NEEDS_USER_INPUT = "needs_user_input"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True)
class ConversationMessage:
    """One message in a conversation session."""

    role: MessageRole | str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.role, MessageRole):
            object.__setattr__(self, "role", MessageRole(self.role))

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "content": self.content,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class TaskSpec:
    """A runnable task derived from a user message."""

    task_id: str
    label: RouteLabel | str
    text: str
    requires_user_input: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", coerce_label(self.label))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "label": self.label.value,
            "text": self.text,
            "requires_user_input": self.requires_user_input,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class TaskExecutionContext:
    """Context passed to a task handler."""

    session: "SessionState"
    route_decision: RouteDecision
    model_selection: ModelSelection
    parent_model_selection: ModelSelection | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskResult:
    """Result from one dispatched task."""

    task: TaskSpec
    content: str
    status: AgentStatus = AgentStatus.COMPLETED
    model_selection: ModelSelection | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.status, AgentStatus):
            object.__setattr__(self, "status", AgentStatus(self.status))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task.to_dict(),
            "content": self.content,
            "status": self.status.value,
            "model_selection": self.model_selection.to_dict() if self.model_selection else None,
            "metadata": self.metadata,
        }


@dataclass
class SessionState:
    """Mutable session state owned by the caller or API layer."""

    session_id: str = field(default_factory=lambda: f"session_{uuid4().hex}")
    messages: list[ConversationMessage] = field(default_factory=list)
    previous_intent: RouteLabel | str | None = None
    conversation_summary: str | None = None
    language: str | None = "zh"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_router_context(self) -> RouterContext:
        return RouterContext(
            previous_intent=self.previous_intent,
            conversation_summary=self.conversation_summary,
            language=self.language,
        )

    def add_message(
        self,
        role: MessageRole | str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> ConversationMessage:
        message = ConversationMessage(role=role, content=content, metadata=metadata or {})
        self.messages.append(message)
        self._refresh_summary()
        return message

    def record_user_message(self, content: str) -> ConversationMessage:
        return self.add_message(MessageRole.USER, content)

    def record_agent_response(self, response: "AgentResponse") -> ConversationMessage:
        self.previous_intent = response.route_decision.label
        return self.add_message(
            MessageRole.ASSISTANT,
            response.content,
            metadata={
                "status": response.status.value,
                "route": response.route_decision.label.value,
                "model_id": response.model_selection.model.model_id,
            },
        )

    def _refresh_summary(self) -> None:
        recent_messages = self.messages[-4:]
        parts = []
        for message in recent_messages:
            content = message.content.strip()
            if len(content) > 80:
                content = content[:77] + "..."
            parts.append(f"{message.role.value}: {content}")
        self.conversation_summary = " | ".join(parts) if parts else None


@dataclass(frozen=True)
class AgentResponse:
    """Response returned by FinanceAgent for one user turn."""

    content: str
    status: AgentStatus
    route_decision: RouteDecision
    model_selection: ModelSelection
    tasks: tuple[TaskSpec, ...]
    task_results: tuple[TaskResult, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.status, AgentStatus):
            object.__setattr__(self, "status", AgentStatus(self.status))

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "status": self.status.value,
            "route_decision": self.route_decision.to_dict(),
            "model_selection": self.model_selection.to_dict(),
            "tasks": [task.to_dict() for task in self.tasks],
            "task_results": [result.to_dict() for result in self.task_results],
            "metadata": self.metadata,
        }
