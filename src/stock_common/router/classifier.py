"""Classifier interfaces and a conservative baseline implementation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from stock_common.router.config import RouterConfig
from stock_common.router.normalization import normalize_text
from stock_common.router.schemas import ClassifierResult, RouteLabel, RouterContext


class Classifier(Protocol):
    """Interface implemented by all route classifiers."""

    def predict(self, text: str, context: RouterContext | None = None) -> ClassifierResult:
        """Predict a top-level route label for the request."""


@dataclass(frozen=True)
class KeywordBaselineClassifier:
    """Small deterministic classifier used until a trained model is connected.

    This is not intended to replace a model. It gives the rest of the router a
    stable classifier-shaped dependency for local development and testing.
    """

    config: RouterConfig = RouterConfig()
    model_version: str = "keyword-baseline-v1"

    def predict(self, text: str, context: RouterContext | None = None) -> ClassifierResult:
        normalized = normalize_text(text)
        if not normalized:
            return ClassifierResult(
                label=RouteLabel.UNKNOWN,
                confidence=0.99,
                probabilities={
                    RouteLabel.UNKNOWN: 0.99,
                    RouteLabel.CHAT: 0.003,
                    RouteLabel.FACTOR_RESEARCH: 0.003,
                    RouteLabel.MIXED: 0.004,
                },
                model_version=self.model_version,
            )

        factor_score = self._score(normalized, self.config.factor_keywords)
        chat_score = self._score(normalized, self.config.chat_keywords)
        multi_action_score = self._score(normalized, self.config.multi_action_keywords)
        context_bias = self._context_bias(context)

        factor_score += context_bias.get(RouteLabel.FACTOR_RESEARCH, 0.0)
        chat_score += context_bias.get(RouteLabel.CHAT, 0.0)

        if factor_score > 0 and chat_score > 0 and multi_action_score > 0:
            probabilities = {
                RouteLabel.MIXED: 0.78,
                RouteLabel.FACTOR_RESEARCH: 0.12,
                RouteLabel.CHAT: 0.08,
                RouteLabel.UNKNOWN: 0.02,
            }
        elif factor_score > chat_score and factor_score > 0:
            confidence = min(0.86, 0.62 + factor_score)
            probabilities = self._probabilities(RouteLabel.FACTOR_RESEARCH, confidence)
        elif chat_score > factor_score and chat_score > 0:
            confidence = min(0.86, 0.62 + chat_score)
            probabilities = self._probabilities(RouteLabel.CHAT, confidence)
        elif context and context.previous_intent:
            previous = RouteLabel(context.previous_intent)
            probabilities = self._probabilities(previous, 0.55)
        else:
            probabilities = {
                RouteLabel.UNKNOWN: 0.55,
                RouteLabel.CHAT: 0.25,
                RouteLabel.FACTOR_RESEARCH: 0.15,
                RouteLabel.MIXED: 0.05,
            }

        label = max(probabilities, key=probabilities.get)
        return ClassifierResult(
            label=label,
            confidence=probabilities[label],
            probabilities=probabilities,
            model_version=self.model_version,
        )

    @staticmethod
    def _score(text: str, keywords: tuple[str, ...]) -> float:
        hits = 0
        for keyword in keywords:
            normalized_keyword = keyword.lower()
            if normalized_keyword == "ic":
                hits += 1 if re.search(r"(^|[^a-z])ic([^a-z]|$)", text) else 0
                continue
            hits += 1 if normalized_keyword in text else 0
        return min(0.28, hits * 0.08)

    @staticmethod
    def _probabilities(top_label: RouteLabel, confidence: float) -> dict[RouteLabel, float]:
        remaining = max(0.0, 1.0 - confidence)
        secondary = remaining * 0.55
        tertiary = remaining * 0.30
        unknown = remaining * 0.15

        if top_label is RouteLabel.CHAT:
            return {
                RouteLabel.CHAT: confidence,
                RouteLabel.FACTOR_RESEARCH: secondary,
                RouteLabel.MIXED: tertiary,
                RouteLabel.UNKNOWN: unknown,
            }

        if top_label is RouteLabel.FACTOR_RESEARCH:
            return {
                RouteLabel.FACTOR_RESEARCH: confidence,
                RouteLabel.CHAT: secondary,
                RouteLabel.MIXED: tertiary,
                RouteLabel.UNKNOWN: unknown,
            }

        return {
            top_label: confidence,
            RouteLabel.CHAT: secondary,
            RouteLabel.FACTOR_RESEARCH: tertiary,
            RouteLabel.UNKNOWN: unknown,
        }

    @staticmethod
    def _context_bias(context: RouterContext | None) -> dict[RouteLabel, float]:
        if not context or not context.previous_intent:
            return {}

        try:
            previous = RouteLabel(context.previous_intent)
        except ValueError:
            return {}

        if previous in {RouteLabel.CHAT, RouteLabel.FACTOR_RESEARCH}:
            return {previous: 0.03}
        return {}
