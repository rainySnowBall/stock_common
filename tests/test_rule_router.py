import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_common.router.rule_router import RuleRouter
from stock_common.router.schemas import RouteLabel


class RuleRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = RuleRouter()

    def test_explicit_backtest_routes_to_factor_research(self) -> None:
        result = self.router.route("帮我回测一下 ROE 因子")

        self.assertTrue(result.is_strong_match)
        self.assertEqual(result.label, RouteLabel.FACTOR_RESEARCH)
        self.assertFalse(result.requires_llm_parser)
        self.assertIn("keyword:回测", result.triggered_rules)

    def test_indicator_explanation_routes_to_chat(self) -> None:
        result = self.router.route("ROE 怎么计算？")

        self.assertTrue(result.is_strong_match)
        self.assertEqual(result.label, RouteLabel.CHAT)
        self.assertFalse(result.requires_llm_parser)

    def test_query_plus_backtest_routes_to_mixed(self) -> None:
        result = self.router.route("先告诉我贵州茅台当前 PE，再回测低 PE 策略")

        self.assertTrue(result.is_strong_match)
        self.assertEqual(result.label, RouteLabel.MIXED)
        self.assertTrue(result.requires_llm_parser)

    def test_multi_action_query_without_strong_chat_keyword_routes_to_mixed(self) -> None:
        result = self.router.route("先查 PE，再回测低 PE 策略")

        self.assertTrue(result.is_strong_match)
        self.assertEqual(result.label, RouteLabel.MIXED)
        self.assertTrue(result.requires_llm_parser)
        self.assertIn("information_query", result.triggered_rules)

    def test_company_historical_explanation_is_not_factor_research(self) -> None:
        result = self.router.route("宁德时代过去十年 ROE 为什么下降？")

        self.assertTrue(result.is_strong_match)
        self.assertEqual(result.label, RouteLabel.CHAT)


if __name__ == "__main__":
    unittest.main()
