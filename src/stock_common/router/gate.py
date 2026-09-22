"""Confidence gate for classifier decisions."""

from __future__ import annotations

from dataclasses import dataclass

from stock_common.router.config import RouterConfig
from stock_common.router.schemas import ClassifierResult, FallbackReason, RouteLabel


@dataclass(frozen=True)
class GateResult:
    """Decision made by the confidence gate."""

    allow_direct_route: bool
    fallback_reason: FallbackReason = FallbackReason.NONE


@dataclass(frozen=True)
class ConfidenceGate:
    """Apply confidence and probability-margin checks."""

    config: RouterConfig = RouterConfig()

    def evaluate(self, result: ClassifierResult) -> GateResult:
        if result.label is RouteLabel.MIXED:
            return GateResult(False, FallbackReason.MIXED)

        if result.label is RouteLabel.UNKNOWN:
            return GateResult(False, FallbackReason.UNKNOWN)

        if result.confidence < self.config.direct_route_threshold:
            return GateResult(False, FallbackReason.LOW_CONFIDENCE)

        if result.margin < self.config.margin_threshold:
            return GateResult(False, FallbackReason.LOW_MARGIN)

        return GateResult(True)
