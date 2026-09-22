import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_common.model_selection import (
    ModelCapability,
    ModelFunction,
    ModelSelectionError,
    ModelSelectionRequest,
    ModelSelector,
    TaskDifficulty,
)
from stock_common.model_selection.defaults import build_model_profiles
from stock_common.router.schemas import (
    DecisionSource,
    FallbackReason,
    RouteDecision,
    RouteLabel,
)


class ModelSelectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.selector = ModelSelector()

    def test_easy_chat_selects_fast_model(self) -> None:
        route = RouteDecision(
            label=RouteLabel.CHAT,
            confidence=0.95,
            source=DecisionSource.RULE,
            requires_llm_parser=False,
        )

        selection = self.selector.select(
            ModelSelectionRequest(text="ROE 是什么？", route_decision=route)
        )

        self.assertEqual(selection.function, ModelFunction.CHAT)
        self.assertEqual(selection.difficulty, TaskDifficulty.EASY)
        self.assertEqual(selection.model.model_id, "finance-chat-fast")
        self.assertEqual(
            selection.model.runtime_env_names["api_key"],
            "FINANCE_CHAT_FAST_API_KEY",
        )
        self.assertEqual(
            selection.model.runtime_env_names["base_url"],
            "FINANCE_CHAT_FAST_BASE_URL",
        )

    def test_medium_chat_selects_balanced_model(self) -> None:
        route = RouteDecision(
            label=RouteLabel.CHAT,
            confidence=0.95,
            source=DecisionSource.RULE,
            requires_llm_parser=False,
        )

        selection = self.selector.select(
            ModelSelectionRequest(text="分析一下贵州茅台净利润变化的原因", route_decision=route)
        )

        self.assertEqual(selection.difficulty, TaskDifficulty.MEDIUM)
        self.assertEqual(selection.model.model_id, "finance-chat-balanced")

    def test_hard_chat_selects_strong_model(self) -> None:
        route = RouteDecision(
            label=RouteLabel.CHAT,
            confidence=0.90,
            source=DecisionSource.CLASSIFIER,
            requires_llm_parser=False,
        )

        selection = self.selector.select(
            ModelSelectionRequest(text="写一份宁德时代估值模型和行业归因的深度分析", route_decision=route)
        )

        self.assertEqual(selection.difficulty, TaskDifficulty.HARD)
        self.assertEqual(selection.model.model_id, "finance-chat-strong")

    def test_factor_research_selects_research_model(self) -> None:
        route = RouteDecision(
            label=RouteLabel.FACTOR_RESEARCH,
            confidence=0.97,
            source=DecisionSource.RULE,
            requires_llm_parser=False,
        )

        selection = self.selector.select(
            ModelSelectionRequest(text="测试过去五年 ROE 因子的表现", route_decision=route)
        )

        self.assertEqual(selection.function, ModelFunction.FACTOR_RESEARCH)
        self.assertEqual(selection.difficulty, TaskDifficulty.MEDIUM)
        self.assertEqual(selection.model.model_id, "finance-research-balanced")

    def test_mixed_route_selects_task_parser_model(self) -> None:
        route = RouteDecision(
            label=RouteLabel.MIXED,
            confidence=0.98,
            source=DecisionSource.RULE,
            requires_llm_parser=True,
            fallback_reason=FallbackReason.MIXED,
        )

        selection = self.selector.select(
            ModelSelectionRequest(text="先查 PE，再回测低 PE 策略", route_decision=route)
        )

        self.assertEqual(selection.function, ModelFunction.TASK_PARSER)
        self.assertEqual(selection.difficulty, TaskDifficulty.HARD)
        self.assertEqual(selection.model.model_id, "finance-research-strong")

    def test_required_capability_can_promote_model(self) -> None:
        route = RouteDecision(
            label=RouteLabel.CHAT,
            confidence=0.95,
            source=DecisionSource.RULE,
            requires_llm_parser=False,
        )

        selection = self.selector.select(
            ModelSelectionRequest(
                text="分析贵州茅台财报",
                route_decision=route,
                required_capabilities=frozenset({ModelCapability.LONG_CONTEXT}),
            )
        )

        self.assertEqual(selection.model.model_id, "finance-chat-strong")
        self.assertIn("fallback_used", selection.metadata)

    def test_explicit_router_classifier_function(self) -> None:
        selection = self.selector.select(
            ModelSelectionRequest(
                text="任意输入",
                function=ModelFunction.ROUTER_CLASSIFIER,
                difficulty=TaskDifficulty.EASY,
            )
        )

        self.assertEqual(selection.model.model_id, "router-classifier-local")

    def test_raises_when_no_model_can_satisfy_request(self) -> None:
        with self.assertRaises(ModelSelectionError):
            self.selector.select(
                ModelSelectionRequest(
                    text="任意输入",
                    function=ModelFunction.ROUTER_CLASSIFIER,
                    required_capabilities=frozenset({ModelCapability.TOOL_CALLING}),
                )
            )

    def test_selector_can_use_external_config_profiles(self) -> None:
        profiles = build_model_profiles(
            (
                {
                    "model_id": "custom-chat-mini",
                    "provider": "custom",
                    "model_name_env": "CUSTOM_CHAT_MINI_MODEL_NAME",
                    "base_url_env": "CUSTOM_CHAT_MINI_BASE_URL",
                    "api_key_env": "CUSTOM_CHAT_MINI_API_KEY",
                    "supported_functions": ("chat",),
                    "supported_difficulties": ("easy",),
                    "capabilities": ("json_output",),
                    "quality_score": 0.60,
                    "latency_score": 0.99,
                    "cost_score": 0.99,
                    "max_input_tokens": 8_000,
                },
            )
        )
        selector = ModelSelector(registry=profiles)
        route = RouteDecision(
            label=RouteLabel.CHAT,
            confidence=0.95,
            source=DecisionSource.RULE,
            requires_llm_parser=False,
        )

        selection = selector.select(
            ModelSelectionRequest(text="PE 是什么？", route_decision=route)
        )

        self.assertEqual(selection.model.model_id, "custom-chat-mini")
        self.assertEqual(selection.model.provider, "custom")
        self.assertEqual(selection.model.runtime_env_names["model_name"], "CUSTOM_CHAT_MINI_MODEL_NAME")
        self.assertEqual(selection.model.runtime_env_names["api_key"], "CUSTOM_CHAT_MINI_API_KEY")

    def test_selection_to_dict_includes_runtime_env_names(self) -> None:
        selection = self.selector.select(
            ModelSelectionRequest(
                text="任意输入",
                function=ModelFunction.ROUTER_CLASSIFIER,
                difficulty=TaskDifficulty.EASY,
            )
        )

        payload = selection.to_dict()

        self.assertEqual(
            payload["runtime_env_names"]["model_path"],
            "ROUTER_CLASSIFIER_MODEL_PATH",
        )


if __name__ == "__main__":
    unittest.main()
