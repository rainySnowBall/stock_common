"""Lightweight deterministic router rules."""

from __future__ import annotations

import re
from dataclasses import dataclass

from stock_common.router.config import RouterConfig
from stock_common.router.normalization import normalize_text
from stock_common.router.schemas import RouteLabel, RouterContext, RuleResult


_FACTOR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"过去[一二三四五六七八九十0-9]+年.*(因子|策略|组合|超额|收益|胜率|有效|表现|有没有用)"),
    re.compile(r"(高|低).*(roe|pe|pb|估值|动量|momentum|质量|股息).*(超额|收益|表现|有效|有没有用)"),
    re.compile(r"(roe|pe|pb|macd|rsi|momentum|动量).*(过去|历史).*(有效|表现|收益|胜率|有没有用)"),
)

_CHAT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(是什么|什么意思|怎么计算|如何计算|解释一下)"),
    re.compile(r"(最新|当前|目前).*(价格|股价|roe|pe|pb|毛利率|净利润|营业收入)"),
    re.compile(r"(为什么).*(上涨|下跌|上升|下降|涨|跌)"),
    re.compile(r"(查一下|看一下|告诉我).*(价格|股价|roe|pe|pb|毛利率|净利润|营业收入|财报|新闻|公告)"),
)


@dataclass(frozen=True)
class RuleRouter:
    """Fast keyword and pattern router.

    The rule layer is intentionally narrow. It only returns strong matches for
    explicit, low-ambiguity requests and leaves boundary cases to the classifier
    or LLM parser.
    """

    config: RouterConfig = RouterConfig()

    def route(self, text: str, context: RouterContext | None = None) -> RuleResult:
        del context
        normalized = normalize_text(text)
        if not normalized:
            return RuleResult(
                label=RouteLabel.UNKNOWN,
                confidence=1.0,
                is_strong_match=True,
                triggered_rules=("empty_text",),
                requires_llm_parser=True,
            )

        factor_hits = self._matched_keywords(normalized, self.config.factor_keywords)
        chat_hits = self._matched_keywords(normalized, self.config.chat_keywords)
        multi_action_hits = self._matched_keywords(normalized, self.config.multi_action_keywords)
        factor_pattern_hits = self._matched_patterns(normalized, _FACTOR_PATTERNS, "factor_pattern")
        chat_pattern_hits = self._matched_patterns(normalized, _CHAT_PATTERNS, "chat_pattern")

        factor_signals = factor_hits + factor_pattern_hits
        chat_signals = chat_hits + chat_pattern_hits

        if factor_signals and chat_signals:
            return RuleResult(
                label=RouteLabel.MIXED,
                confidence=0.98,
                is_strong_match=True,
                triggered_rules=tuple(factor_signals + chat_signals + multi_action_hits),
                requires_llm_parser=True,
            )

        if factor_signals and multi_action_hits and self._has_information_query(normalized):
            return RuleResult(
                label=RouteLabel.MIXED,
                confidence=0.96,
                is_strong_match=True,
                triggered_rules=tuple(factor_signals + multi_action_hits + ["information_query"]),
                requires_llm_parser=True,
            )

        if factor_signals:
            return RuleResult(
                label=RouteLabel.FACTOR_RESEARCH,
                confidence=0.97,
                is_strong_match=True,
                triggered_rules=tuple(factor_signals),
            )

        if chat_signals:
            return RuleResult(
                label=RouteLabel.CHAT,
                confidence=0.95,
                is_strong_match=True,
                triggered_rules=tuple(chat_signals),
            )

        return RuleResult(label=None, confidence=0.0, is_strong_match=False)

    @staticmethod
    def _matched_keywords(text: str, keywords: tuple[str, ...]) -> list[str]:
        matches: list[str] = []
        for keyword in keywords:
            normalized_keyword = keyword.lower()
            if normalized_keyword == "ic":
                if re.search(r"(^|[^a-z])ic([^a-z]|$)", text):
                    matches.append("keyword:ic")
                continue

            if normalized_keyword in text:
                matches.append(f"keyword:{keyword}")
        return matches

    @staticmethod
    def _matched_patterns(
        text: str,
        patterns: tuple[re.Pattern[str], ...],
        prefix: str,
    ) -> list[str]:
        return [f"{prefix}:{index}" for index, pattern in enumerate(patterns) if pattern.search(text)]

    @staticmethod
    def _has_information_query(text: str) -> bool:
        information_verbs = ("查", "看", "告诉", "是多少", "当前", "目前", "最新")
        return any(verb in text for verb in information_verbs)
