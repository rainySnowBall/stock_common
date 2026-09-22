import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_common.router.config import RouterConfig
from stock_common.router.router import Router
from stock_common.router.schemas import (
    ClassifierResult,
    DecisionSource,
    FallbackReason,
    RouteDecision,
    RouteLabel,
)
from stock_common.router.telemetry import InMemoryRouteLogger


class StaticClassifier:
    def __init__(self, result: ClassifierResult) -> None:
        self.result = result
        self.calls = 0

    def predict(self, text, context=None):
        del text, context
        self.calls += 1
        return self.result


class FailingClassifier:
    def predict(self, text, context=None):
        del text, context
        raise RuntimeError("classifier unavailable")


class StaticLLMRouter:
    def __init__(self, label: RouteLabel = RouteLabel.FACTOR_RESEARCH) -> None:
        self.label = label
        self.calls = 0
        self.last_reason = None

    async def route(
        self,
        text,
        context,
        fallback_reason,
        rule_result=None,
        classifier_result=None,
    ):
        del text, context, rule_result, classifier_result
        self.calls += 1
        self.last_reason = fallback_reason
        return RouteDecision(
            label=self.label,
            confidence=0.93,
            source=DecisionSource.LLM,
            requires_llm_parser=False,
            fallback_reason=fallback_reason,
        )


class RouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_strong_rule_bypasses_classifier(self) -> None:
        classifier = StaticClassifier(
            ClassifierResult(
                label=RouteLabel.CHAT,
                confidence=0.99,
                probabilities={RouteLabel.CHAT: 0.99, RouteLabel.FACTOR_RESEARCH: 0.01},
            )
        )
        router = Router(classifier=classifier)

        decision = await router.route("帮我回测 ROE 因子")

        self.assertEqual(classifier.calls, 0)
        self.assertEqual(decision.label, RouteLabel.FACTOR_RESEARCH)
        self.assertEqual(decision.source, DecisionSource.RULE)

    async def test_high_confidence_classifier_routes_directly(self) -> None:
        classifier = StaticClassifier(
            ClassifierResult(
                label=RouteLabel.CHAT,
                confidence=0.94,
                probabilities={
                    RouteLabel.CHAT: 0.94,
                    RouteLabel.FACTOR_RESEARCH: 0.03,
                    RouteLabel.MIXED: 0.02,
                    RouteLabel.UNKNOWN: 0.01,
                },
                model_version="test-classifier",
            )
        )
        router = Router(
            config=RouterConfig(rule_router_enabled=False),
            classifier=classifier,
        )

        decision = await router.route("普通金融问答")

        self.assertEqual(decision.label, RouteLabel.CHAT)
        self.assertEqual(decision.source, DecisionSource.CLASSIFIER)
        self.assertFalse(decision.requires_llm_parser)
        self.assertEqual(decision.model_version, "test-classifier")

    async def test_low_confidence_classifier_uses_llm_fallback(self) -> None:
        classifier = StaticClassifier(
            ClassifierResult(
                label=RouteLabel.FACTOR_RESEARCH,
                confidence=0.81,
                probabilities={
                    RouteLabel.FACTOR_RESEARCH: 0.81,
                    RouteLabel.CHAT: 0.13,
                    RouteLabel.MIXED: 0.04,
                    RouteLabel.UNKNOWN: 0.02,
                },
            )
        )
        llm_router = StaticLLMRouter(RouteLabel.FACTOR_RESEARCH)
        router = Router(
            config=RouterConfig(rule_router_enabled=False),
            classifier=classifier,
            llm_router=llm_router,
        )

        decision = await router.route("ROE 这个东西过去几年还有用吗？")

        self.assertEqual(llm_router.calls, 1)
        self.assertEqual(llm_router.last_reason, FallbackReason.LOW_CONFIDENCE)
        self.assertEqual(decision.source, DecisionSource.LLM)
        self.assertEqual(decision.label, RouteLabel.FACTOR_RESEARCH)

    async def test_classifier_exception_falls_back_to_default(self) -> None:
        router = Router(
            config=RouterConfig(rule_router_enabled=False, llm_fallback_enabled=False),
            classifier=FailingClassifier(),
        )

        decision = await router.route("随便问一个问题")

        self.assertEqual(decision.source, DecisionSource.DEFAULT)
        self.assertEqual(decision.label, RouteLabel.CHAT)
        self.assertTrue(decision.requires_llm_parser)
        self.assertEqual(decision.fallback_reason, FallbackReason.CLASSIFIER_ERROR)

    async def test_route_logger_receives_record(self) -> None:
        logger = InMemoryRouteLogger()
        router = Router(logger=logger)

        decision = await router.route("MACD 的 DIF 和 DEA 有什么区别？", request_id="req_test")

        self.assertEqual(len(logger.records), 1)
        self.assertEqual(logger.records[0].request_id, "req_test")
        self.assertEqual(logger.records[0].final_decision, decision)
        self.assertIn("latency_ms", decision.metadata)


if __name__ == "__main__":
    unittest.main()
