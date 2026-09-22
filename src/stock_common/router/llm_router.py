"""LLM fallback abstraction.

The default implementation is deliberately conservative and does not call an
external model. Production systems should inject an implementation that calls
the actual LLM task parser.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from stock_common.router.schemas import (
    ClassifierResult,
    DecisionSource,
    FallbackReason,
    RouteDecision,
    RouteLabel,
    RouterContext,
    RuleResult,
)


class LLMRouter(Protocol):
    """Interface implemented by LLM-powered fallback routers."""

    async def route(
        self,
        text: str,
        context: RouterContext | None,
        fallback_reason: FallbackReason,
        rule_result: RuleResult | None = None,
        classifier_result: ClassifierResult | None = None,
    ) -> RouteDecision:
        """Resolve a route for ambiguous or mixed requests."""


@dataclass(frozen=True)
class ConservativeFallbackRouter:
    """Fallback used when no real LLM router has been wired yet."""

    def route_label(
        self,
        fallback_reason: FallbackReason,
        rule_result: RuleResult | None,
        classifier_result: ClassifierResult | None,
    ) -> RouteLabel:
        if fallback_reason is FallbackReason.EMPTY_TEXT:
            return RouteLabel.UNKNOWN

        if rule_result and rule_result.label:
            return rule_result.label

        if classifier_result:
            return classifier_result.label

        return RouteLabel.UNKNOWN

    async def route(
        self,
        text: str,
        context: RouterContext | None,
        fallback_reason: FallbackReason,
        rule_result: RuleResult | None = None,
        classifier_result: ClassifierResult | None = None,
    ) -> RouteDecision:
        del text, context
        label = self.route_label(fallback_reason, rule_result, classifier_result)
        confidence = self._confidence(rule_result, classifier_result)

        return RouteDecision(
            label=label,
            confidence=confidence,
            source=DecisionSource.DEFAULT,
            requires_llm_parser=True,
            model_version=classifier_result.model_version if classifier_result else None,
            probabilities=classifier_result.probabilities if classifier_result else {},
            fallback_reason=fallback_reason,
            triggered_rules=rule_result.triggered_rules if rule_result else (),
        )

    @staticmethod
    def _confidence(
        rule_result: RuleResult | None,
        classifier_result: ClassifierResult | None,
    ) -> float:
        candidates = []
        if rule_result and rule_result.is_match:
            candidates.append(rule_result.confidence)
        if classifier_result:
            candidates.append(classifier_result.confidence)
        if not candidates:
            return 0.50
        return min(0.89, max(candidates))
