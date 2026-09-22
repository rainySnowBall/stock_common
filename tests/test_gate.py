import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_common.router.gate import ConfidenceGate
from stock_common.router.config import RouterConfig
from stock_common.router.schemas import ClassifierResult, FallbackReason, RouteLabel


class ConfidenceGateTests(unittest.TestCase):
    def test_allows_high_confidence_and_high_margin(self) -> None:
        result = ClassifierResult(
            label=RouteLabel.CHAT,
            confidence=0.94,
            probabilities={
                RouteLabel.CHAT: 0.94,
                RouteLabel.FACTOR_RESEARCH: 0.03,
                RouteLabel.MIXED: 0.02,
                RouteLabel.UNKNOWN: 0.01,
            },
        )

        gate_result = ConfidenceGate().evaluate(result)

        self.assertTrue(gate_result.allow_direct_route)
        self.assertEqual(gate_result.fallback_reason, FallbackReason.NONE)

    def test_blocks_low_confidence(self) -> None:
        result = ClassifierResult(
            label=RouteLabel.FACTOR_RESEARCH,
            confidence=0.81,
            probabilities={
                RouteLabel.FACTOR_RESEARCH: 0.81,
                RouteLabel.CHAT: 0.12,
                RouteLabel.MIXED: 0.05,
                RouteLabel.UNKNOWN: 0.02,
            },
        )

        gate_result = ConfidenceGate().evaluate(result)

        self.assertFalse(gate_result.allow_direct_route)
        self.assertEqual(gate_result.fallback_reason, FallbackReason.LOW_CONFIDENCE)

    def test_blocks_low_margin(self) -> None:
        result = ClassifierResult(
            label=RouteLabel.CHAT,
            confidence=0.52,
            probabilities={
                RouteLabel.CHAT: 0.52,
                RouteLabel.FACTOR_RESEARCH: 0.44,
                RouteLabel.MIXED: 0.03,
                RouteLabel.UNKNOWN: 0.01,
            },
        )

        gate_result = ConfidenceGate(RouterConfig(direct_route_threshold=0.50)).evaluate(result)

        self.assertFalse(gate_result.allow_direct_route)
        self.assertEqual(gate_result.fallback_reason, FallbackReason.LOW_MARGIN)

    def test_blocks_mixed_and_unknown(self) -> None:
        mixed_result = ClassifierResult(label=RouteLabel.MIXED, confidence=0.95)
        unknown_result = ClassifierResult(label=RouteLabel.UNKNOWN, confidence=0.95)

        self.assertEqual(ConfidenceGate().evaluate(mixed_result).fallback_reason, FallbackReason.MIXED)
        self.assertEqual(
            ConfidenceGate().evaluate(unknown_result).fallback_reason,
            FallbackReason.UNKNOWN,
        )


if __name__ == "__main__":
    unittest.main()
