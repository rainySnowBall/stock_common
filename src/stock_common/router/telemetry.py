"""Route telemetry hooks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import uuid4

from stock_common.router.schemas import ClassifierResult, RouteDecision, RuleResult


@dataclass(frozen=True)
class RouteLogRecord:
    """Structured route event suitable for JSON logging."""

    request_id: str
    text: str
    final_decision: RouteDecision
    latency_ms: float
    rule_result: RuleResult | None = None
    classifier_result: ClassifierResult | None = None
    correct_label: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "text": self.text,
            "rule_result": self.rule_result.label.value if self.rule_result and self.rule_result.label else None,
            "classifier_label": self.classifier_result.label.value if self.classifier_result else None,
            "classifier_confidence": self.classifier_result.confidence if self.classifier_result else None,
            "final_route": self.final_decision.label.value,
            "source": self.final_decision.source.value,
            "model_version": self.final_decision.model_version,
            "latency_ms": self.latency_ms,
            "fallback_reason": self.final_decision.fallback_reason.value,
            "correct_label": self.correct_label,
        }


class RouteLogger(Protocol):
    """Telemetry sink for route decisions."""

    def log(self, record: RouteLogRecord) -> None:
        """Persist or emit a route log record."""


@dataclass
class NullRouteLogger:
    """Logger that intentionally drops route events."""

    def log(self, record: RouteLogRecord) -> None:
        del record


@dataclass
class InMemoryRouteLogger:
    """Small logger useful for tests, notebooks, and local demos."""

    records: list[RouteLogRecord] = field(default_factory=list)

    def log(self, record: RouteLogRecord) -> None:
        self.records.append(record)


def new_request_id() -> str:
    return f"req_{uuid4().hex}"
