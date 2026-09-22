"""Data contracts for adaptive model selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from stock_common.router.schemas import RouteDecision, RouteLabel


class TaskDifficulty(str, Enum):
    """Coarse task difficulty levels used by selection policy."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class ModelFunction(str, Enum):
    """Functional model lanes in the finance agent system."""

    CHAT = "chat"
    FACTOR_RESEARCH = "factor_research"
    TASK_PARSER = "task_parser"
    ROUTER_CLASSIFIER = "router_classifier"
    UNKNOWN = "unknown"


class ModelCapability(str, Enum):
    """Capabilities a caller may require from a selected model."""

    JSON_OUTPUT = "json_output"
    TOOL_CALLING = "tool_calling"
    LONG_CONTEXT = "long_context"
    REASONING = "reasoning"


class ModelSelectionError(RuntimeError):
    """Raised when no configured model can satisfy a selection request."""


@dataclass(frozen=True)
class ModelProfile:
    """Configurable profile for one model option.

    Scores are normalized to 0.0 - 1.0 and higher is better:
    quality_score means better answers, latency_score means faster responses,
    and cost_score means cheaper use.
    """

    model_id: str
    supported_functions: frozenset[ModelFunction]
    supported_difficulties: frozenset[TaskDifficulty]
    provider: str = "configured"
    display_name: str | None = None
    capabilities: frozenset[ModelCapability] = frozenset()
    model_name_env: str | None = None
    base_url_env: str | None = None
    api_key_env: str | None = None
    timeout_seconds_env: str | None = None
    max_retries_env: str | None = None
    model_path_env: str | None = None
    model_version_env: str | None = None
    quality_score: float = 0.5
    latency_score: float = 0.5
    cost_score: float = 0.5
    max_input_tokens: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.model_id:
            raise ValueError("model_id is required")
        if not self.supported_functions:
            raise ValueError("supported_functions cannot be empty")
        if not self.supported_difficulties:
            raise ValueError("supported_difficulties cannot be empty")

        object.__setattr__(
            self,
            "supported_functions",
            frozenset(coerce_function(function) for function in self.supported_functions),
        )
        object.__setattr__(
            self,
            "supported_difficulties",
            frozenset(coerce_difficulty(difficulty) for difficulty in self.supported_difficulties),
        )
        object.__setattr__(
            self,
            "capabilities",
            frozenset(coerce_capability(capability) for capability in self.capabilities),
        )

        for field_name in ("quality_score", "latency_score", "cost_score"):
            score = getattr(self, field_name)
            if not 0.0 <= score <= 1.0:
                raise ValueError(f"{field_name} must be between 0.0 and 1.0")

        if self.max_input_tokens is not None and self.max_input_tokens <= 0:
            raise ValueError("max_input_tokens must be positive")

    @property
    def name(self) -> str:
        return self.display_name or self.model_id

    @property
    def runtime_env_names(self) -> dict[str, str]:
        """Environment variable names needed to construct a runtime client."""

        candidates = {
            "model_name": self.model_name_env,
            "base_url": self.base_url_env,
            "api_key": self.api_key_env,
            "timeout_seconds": self.timeout_seconds_env,
            "max_retries": self.max_retries_env,
            "model_path": self.model_path_env,
            "model_version": self.model_version_env,
        }
        return {key: value for key, value in candidates.items() if value}

    def supports(
        self,
        function: ModelFunction,
        difficulty: TaskDifficulty,
        required_capabilities: frozenset[ModelCapability],
        estimated_input_tokens: int,
    ) -> bool:
        if function not in self.supported_functions:
            return False
        if difficulty not in self.supported_difficulties:
            return False
        if not required_capabilities.issubset(self.capabilities):
            return False
        if self.max_input_tokens is not None and estimated_input_tokens > self.max_input_tokens:
            return False
        return True


@dataclass(frozen=True)
class ModelSelectionRequest:
    """Request consumed by the model selector."""

    text: str
    route_decision: RouteDecision | None = None
    function: ModelFunction | RouteLabel | str | None = None
    difficulty: TaskDifficulty | str | None = None
    required_capabilities: frozenset[ModelCapability] = frozenset()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.function is not None:
            object.__setattr__(self, "function", coerce_function(self.function))
        if self.difficulty is not None:
            object.__setattr__(self, "difficulty", coerce_difficulty(self.difficulty))
        object.__setattr__(
            self,
            "required_capabilities",
            frozenset(coerce_capability(capability) for capability in self.required_capabilities),
        )


@dataclass(frozen=True)
class ModelSelection:
    """Final model choice plus explainable selection metadata."""

    model: ModelProfile
    function: ModelFunction
    difficulty: TaskDifficulty
    score: float
    reason: str
    alternatives: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model.model_id,
            "provider": self.model.provider,
            "function": self.function.value,
            "difficulty": self.difficulty.value,
            "score": self.score,
            "reason": self.reason,
            "alternatives": list(self.alternatives),
            "runtime_env_names": self.model.runtime_env_names,
            "metadata": self.metadata,
        }


def coerce_difficulty(value: TaskDifficulty | str) -> TaskDifficulty:
    if isinstance(value, TaskDifficulty):
        return value
    return TaskDifficulty(value)


def coerce_function(value: ModelFunction | RouteLabel | str) -> ModelFunction:
    if isinstance(value, ModelFunction):
        return value
    if isinstance(value, RouteLabel):
        return function_from_route_label(value)

    try:
        return ModelFunction(value)
    except ValueError:
        return function_from_route_label(RouteLabel(value))


def coerce_capability(value: ModelCapability | str) -> ModelCapability:
    if isinstance(value, ModelCapability):
        return value
    return ModelCapability(value)


def function_from_route_label(label: RouteLabel) -> ModelFunction:
    if label is RouteLabel.CHAT:
        return ModelFunction.CHAT
    if label is RouteLabel.FACTOR_RESEARCH:
        return ModelFunction.FACTOR_RESEARCH
    if label in {RouteLabel.MIXED, RouteLabel.UNKNOWN}:
        return ModelFunction.TASK_PARSER
    return ModelFunction.UNKNOWN
