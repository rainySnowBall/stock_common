"""Task difficulty estimation for adaptive model selection."""

from __future__ import annotations

import re
from dataclasses import dataclass

from stock_common.model_selection.schemas import (
    ModelFunction,
    ModelSelectionRequest,
    TaskDifficulty,
    function_from_route_label,
)
from stock_common.router.normalization import normalize_text
from stock_common.router.schemas import FallbackReason, RouteLabel


_HARD_KEYWORDS: tuple[str, ...] = (
    "多因子",
    "组合优化",
    "风险模型",
    "行业中性",
    "市值中性",
    "归因",
    "滚动",
    "交易成本",
    "调仓成本",
    "全市场",
    "多市场",
    "约束",
    "深度分析",
    "研报",
    "预测",
    "估值模型",
    "回测框架",
)

_MEDIUM_KEYWORDS: tuple[str, ...] = (
    "分析",
    "比较",
    "对比",
    "为什么",
    "原因",
    "历史",
    "过去",
    "策略",
    "因子",
    "收益",
    "筛选",
    "财报",
)

_EASY_KEYWORDS: tuple[str, ...] = (
    "是什么",
    "什么意思",
    "怎么计算",
    "是多少",
    "最新价格",
    "当前价格",
)

_LONG_PERIOD_RE = re.compile(r"过去([一二三四五六七八九十0-9]+)年")


@dataclass(frozen=True)
class DifficultyEstimator:
    """Estimate model difficulty from routing metadata and request text."""

    medium_text_length: int = 80
    hard_text_length: int = 220

    def estimate(self, request: ModelSelectionRequest, function: ModelFunction) -> TaskDifficulty:
        if request.difficulty is not None:
            return request.difficulty

        text = normalize_text(request.text)
        route_decision = request.route_decision

        if not text:
            return TaskDifficulty.EASY

        if route_decision:
            if route_decision.label in {RouteLabel.MIXED, RouteLabel.UNKNOWN}:
                return TaskDifficulty.HARD

            if route_decision.fallback_reason in {
                FallbackReason.LOW_CONFIDENCE,
                FallbackReason.LOW_MARGIN,
                FallbackReason.RULE_CLASSIFIER_CONFLICT,
                FallbackReason.MULTI_ACTION,
            }:
                return TaskDifficulty.HARD

            if route_decision.requires_llm_parser:
                return TaskDifficulty.HARD

        if function is ModelFunction.TASK_PARSER:
            return TaskDifficulty.HARD

        if self._has_any(text, _HARD_KEYWORDS):
            return TaskDifficulty.HARD

        if function is ModelFunction.FACTOR_RESEARCH:
            if self._is_long_horizon(text) or self._has_any(text, _MEDIUM_KEYWORDS):
                return TaskDifficulty.MEDIUM
            return TaskDifficulty.MEDIUM

        if function is ModelFunction.CHAT:
            if self._has_any(text, _HARD_KEYWORDS):
                return TaskDifficulty.HARD
            if len(text) >= self.hard_text_length:
                return TaskDifficulty.HARD
            if len(text) >= self.medium_text_length or self._has_any(text, _MEDIUM_KEYWORDS):
                return TaskDifficulty.MEDIUM
            if self._has_any(text, _EASY_KEYWORDS):
                return TaskDifficulty.EASY
            return TaskDifficulty.EASY

        if route_decision:
            route_function = function_from_route_label(route_decision.label)
            if route_function is ModelFunction.FACTOR_RESEARCH:
                return TaskDifficulty.MEDIUM

        return TaskDifficulty.MEDIUM

    @staticmethod
    def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
        return any(keyword in text for keyword in keywords)

    @staticmethod
    def _is_long_horizon(text: str) -> bool:
        match = _LONG_PERIOD_RE.search(text)
        if not match:
            return False

        period = match.group(1)
        if period.isdigit():
            return int(period) >= 5

        return period in {"五", "六", "七", "八", "九", "十"}
