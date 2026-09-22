"""Finance Agent Router orchestration."""

from __future__ import annotations

import time
from dataclasses import dataclass

from stock_common.router.classifier import Classifier, KeywordBaselineClassifier
from stock_common.router.config import RouterConfig
from stock_common.router.gate import ConfidenceGate
from stock_common.router.llm_router import ConservativeFallbackRouter, LLMRouter
from stock_common.router.rule_router import RuleRouter
from stock_common.router.schemas import (
    ClassifierResult,
    DecisionSource,
    FallbackReason,
    RouteDecision,
    RouteLabel,
    RouterContext,
    RuleResult,
)
from stock_common.router.telemetry import (
    NullRouteLogger,
    RouteLogger,
    RouteLogRecord,
    new_request_id,
)


@dataclass
class Router:
    """Route natural-language finance requests to the right workflow."""

    config: RouterConfig = RouterConfig()
    rule_router: RuleRouter | None = None
    classifier: Classifier | None = None
    confidence_gate: ConfidenceGate | None = None
    llm_router: LLMRouter | None = None
    logger: RouteLogger | None = None

    def __post_init__(self) -> None:
        if self.rule_router is None:
            self.rule_router = RuleRouter(self.config)
        if self.classifier is None:
            self.classifier = KeywordBaselineClassifier(self.config)
        if self.confidence_gate is None:
            self.confidence_gate = ConfidenceGate(self.config)
        if self.llm_router is None:
            self.llm_router = ConservativeFallbackRouter()
        if self.logger is None:
            self.logger = NullRouteLogger()

    async def route(
        self,
        text: str,
        context: RouterContext | None = None,
        request_id: str | None = None,
    ) -> RouteDecision:
        """Return a route decision for a user request."""

        started_at = time.perf_counter()
        request_id = request_id or new_request_id()
        rule_result: RuleResult | None = None
        classifier_result: ClassifierResult | None = None

        try:
            if self.config.rule_router_enabled and self.rule_router:
                rule_result = self.rule_router.route(text, context)
                if rule_result.is_strong_match:
                    decision = self._decision_from_rule(rule_result)
                    return self._finish(request_id, text, decision, started_at, rule_result, classifier_result)

            if not self.config.classifier_enabled or self.classifier is None:
                decision = await self._fallback(
                    text,
                    context,
                    FallbackReason.UNKNOWN,
                    rule_result,
                    classifier_result,
                )
                return self._finish(request_id, text, decision, started_at, rule_result, classifier_result)

            try:
                classifier_result = self.classifier.predict(text, context)
            except Exception:
                decision = await self._fallback(
                    text,
                    context,
                    FallbackReason.CLASSIFIER_ERROR,
                    rule_result,
                    classifier_result,
                )
                return self._finish(request_id, text, decision, started_at, rule_result, classifier_result)

            conflict_reason = self._rule_classifier_conflict(rule_result, classifier_result)
            if conflict_reason:
                decision = await self._fallback(
                    text,
                    context,
                    conflict_reason,
                    rule_result,
                    classifier_result,
                )
                return self._finish(request_id, text, decision, started_at, rule_result, classifier_result)

            assert self.confidence_gate is not None
            gate_result = self.confidence_gate.evaluate(classifier_result)
            if not gate_result.allow_direct_route:
                decision = await self._fallback(
                    text,
                    context,
                    gate_result.fallback_reason,
                    rule_result,
                    classifier_result,
                )
                return self._finish(request_id, text, decision, started_at, rule_result, classifier_result)

            decision = RouteDecision(
                label=classifier_result.label,
                confidence=classifier_result.confidence,
                source=DecisionSource.CLASSIFIER,
                requires_llm_parser=False,
                model_version=classifier_result.model_version,
                probabilities=classifier_result.probabilities,
            )
            return self._finish(request_id, text, decision, started_at, rule_result, classifier_result)
        except Exception:
            decision = self._failure_default(rule_result)
            return self._finish(request_id, text, decision, started_at, rule_result, classifier_result)

    def _decision_from_rule(self, rule_result: RuleResult) -> RouteDecision:
        if rule_result.label is None:
            raise ValueError("rule_result.label is required for strong rule decisions")

        fallback_reason = FallbackReason.NONE
        if rule_result.label is RouteLabel.MIXED:
            fallback_reason = FallbackReason.MIXED
        elif rule_result.label is RouteLabel.UNKNOWN:
            fallback_reason = FallbackReason.EMPTY_TEXT

        return RouteDecision(
            label=rule_result.label,
            confidence=rule_result.confidence,
            source=DecisionSource.RULE,
            requires_llm_parser=rule_result.requires_llm_parser,
            fallback_reason=fallback_reason,
            triggered_rules=rule_result.triggered_rules,
        )

    async def _fallback(
        self,
        text: str,
        context: RouterContext | None,
        fallback_reason: FallbackReason,
        rule_result: RuleResult | None,
        classifier_result: ClassifierResult | None,
    ) -> RouteDecision:
        if not self.config.llm_fallback_enabled or self.llm_router is None:
            return self._failure_default(rule_result, fallback_reason)

        try:
            return await self.llm_router.route(
                text=text,
                context=context,
                fallback_reason=fallback_reason,
                rule_result=rule_result,
                classifier_result=classifier_result,
            )
        except Exception:
            return self._failure_default(rule_result, FallbackReason.LLM_ERROR)

    @staticmethod
    def _rule_classifier_conflict(
        rule_result: RuleResult | None,
        classifier_result: ClassifierResult,
    ) -> FallbackReason | None:
        if not rule_result or not rule_result.is_match or not rule_result.label:
            return None

        if rule_result.label in {RouteLabel.MIXED, RouteLabel.UNKNOWN}:
            return None

        if classifier_result.label in {RouteLabel.MIXED, RouteLabel.UNKNOWN}:
            return None

        if rule_result.label is not classifier_result.label:
            return FallbackReason.RULE_CLASSIFIER_CONFLICT

        return None

    @staticmethod
    def _failure_default(
        rule_result: RuleResult | None,
        fallback_reason: FallbackReason = FallbackReason.LLM_ERROR,
    ) -> RouteDecision:
        label = RouteLabel.CHAT
        triggered_rules: tuple[str, ...] = ()
        if rule_result and rule_result.label is RouteLabel.FACTOR_RESEARCH:
            label = RouteLabel.FACTOR_RESEARCH
            triggered_rules = rule_result.triggered_rules

        return RouteDecision(
            label=label,
            confidence=0.50,
            source=DecisionSource.DEFAULT,
            requires_llm_parser=True,
            fallback_reason=fallback_reason,
            triggered_rules=triggered_rules,
        )

    def _finish(
        self,
        request_id: str,
        text: str,
        decision: RouteDecision,
        started_at: float,
        rule_result: RuleResult | None,
        classifier_result: ClassifierResult | None,
    ) -> RouteDecision:
        latency_ms = round((time.perf_counter() - started_at) * 1000, 3)
        decision_with_metadata = RouteDecision(
            label=decision.label,
            confidence=decision.confidence,
            source=decision.source,
            requires_llm_parser=decision.requires_llm_parser,
            model_version=decision.model_version,
            probabilities=decision.probabilities,
            fallback_reason=decision.fallback_reason,
            triggered_rules=decision.triggered_rules,
            metadata={**decision.metadata, "request_id": request_id, "latency_ms": latency_ms},
        )

        assert self.logger is not None
        self.logger.log(
            RouteLogRecord(
                request_id=request_id,
                text=text,
                final_decision=decision_with_metadata,
                latency_ms=latency_ms,
                rule_result=rule_result,
                classifier_result=classifier_result,
            )
        )
        return decision_with_metadata
