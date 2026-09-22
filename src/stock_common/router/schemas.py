"""Router data contracts.

These contracts intentionally avoid framework dependencies. They can be mapped
to Pydantic, FastAPI, protobuf, or a Harness-native schema at the integration
boundary without changing router internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RouteLabel(str, Enum):
    """Top-level routes supported by Router V1."""

    CHAT = "chat"
    FACTOR_RESEARCH = "factor_research"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class DecisionSource(str, Enum):
    """Component that produced the final route decision."""

    RULE = "rule"
    CLASSIFIER = "classifier"
    LLM = "llm"
    DEFAULT = "default"


class FallbackReason(str, Enum):
    """Why the request was delegated to a slower or safer parser."""

    NONE = "none"
    LOW_CONFIDENCE = "low_confidence"
    LOW_MARGIN = "low_margin"
    MIXED = "mixed"
    UNKNOWN = "unknown"
    MULTI_ACTION = "multi_action"
    RULE_CLASSIFIER_CONFLICT = "rule_classifier_conflict"
    CLASSIFIER_ERROR = "classifier_error"
    LLM_ERROR = "llm_error"
    EMPTY_TEXT = "empty_text"


@dataclass(frozen=True)
class RouterContext:
    """Short explicit context supplied to the router.

    Keep this object compact. The classifier should receive the current query,
    the most recent intent, and a short summary at most.
    """

    previous_intent: RouteLabel | str | None = None
    conversation_summary: str | None = None
    language: str | None = None


@dataclass(frozen=True)
class ClassifierResult:
    """Normalized output from a classifier implementation."""

    label: RouteLabel
    confidence: float
    probabilities: dict[RouteLabel, float] = field(default_factory=dict)
    model_version: str | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")

        normalized_probabilities: dict[RouteLabel, float] = {}
        for label, probability in self.probabilities.items():
            route_label = coerce_label(label)
            if not 0.0 <= probability <= 1.0:
                raise ValueError("probabilities must be between 0.0 and 1.0")
            normalized_probabilities[route_label] = probability

        object.__setattr__(self, "label", coerce_label(self.label))
        object.__setattr__(self, "probabilities", normalized_probabilities)

    @property
    def margin(self) -> float:
        """Difference between the two highest class probabilities."""

        if len(self.probabilities) < 2:
            return self.confidence

        values = sorted(self.probabilities.values(), reverse=True)
        return values[0] - values[1]


@dataclass(frozen=True)
class RuleResult:
    """Result returned by the lightweight rule router."""

    label: RouteLabel | None
    confidence: float
    is_strong_match: bool
    triggered_rules: tuple[str, ...] = ()
    requires_llm_parser: bool = False

    @property
    def is_match(self) -> bool:
        return self.label is not None


@dataclass(frozen=True)
class RouteDecision:
    """Final route decision consumed by the Harness."""

    label: RouteLabel
    confidence: float
    source: DecisionSource
    requires_llm_parser: bool
    model_version: str | None = None
    probabilities: dict[RouteLabel, float] = field(default_factory=dict)
    fallback_reason: FallbackReason = FallbackReason.NONE
    triggered_rules: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")

        object.__setattr__(self, "label", coerce_label(self.label))
        object.__setattr__(self, "source", coerce_source(self.source))
        object.__setattr__(self, "fallback_reason", coerce_fallback_reason(self.fallback_reason))
        object.__setattr__(
            self,
            "probabilities",
            {coerce_label(label): probability for label, probability in self.probabilities.items()},
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation for API responses/logs."""

        return {
            "label": self.label.value,
            "confidence": self.confidence,
            "source": self.source.value,
            "requires_llm_parser": self.requires_llm_parser,
            "model_version": self.model_version,
            "probabilities": {
                label.value: probability for label, probability in self.probabilities.items()
            },
            "fallback_reason": self.fallback_reason.value,
            "triggered_rules": list(self.triggered_rules),
            "metadata": self.metadata,
        }


def coerce_label(value: RouteLabel | str) -> RouteLabel:
    if isinstance(value, RouteLabel):
        return value
    return RouteLabel(value)


def coerce_source(value: DecisionSource | str) -> DecisionSource:
    if isinstance(value, DecisionSource):
        return value
    return DecisionSource(value)


def coerce_fallback_reason(value: FallbackReason | str) -> FallbackReason:
    if isinstance(value, FallbackReason):
        return value
    return FallbackReason(value)
