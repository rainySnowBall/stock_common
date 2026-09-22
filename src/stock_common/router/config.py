"""Router configuration defaults."""

from __future__ import annotations

from dataclasses import dataclass, field


DEFAULT_FACTOR_KEYWORDS: tuple[str, ...] = (
    "回测",
    "因子有效性",
    "rankic",
    "rank ic",
    "icir",
    "ic",
    "信息系数",
    "分组收益",
    "多空收益",
    "多空组合",
    "历史表现",
    "策略收益",
    "年化收益",
    "最大回撤",
    "夏普",
    "sharpe",
    "调仓",
    "换手率",
    "超额收益",
    "胜率",
    "历史检验",
)

DEFAULT_CHAT_KEYWORDS: tuple[str, ...] = (
    "是什么",
    "什么意思",
    "怎么计算",
    "如何计算",
    "解释一下",
    "查一下",
    "看一下",
    "告诉我",
    "最新价格",
    "当前价格",
    "当前",
    "目前",
    "股价",
    "财报",
    "毛利率",
    "净利润",
    "营业收入",
    "新闻",
    "公告",
    "为什么上涨",
    "为什么下跌",
    "为什么涨",
    "为什么跌",
    "是多少",
)

DEFAULT_MULTI_ACTION_KEYWORDS: tuple[str, ...] = (
    "先",
    "再",
    "然后",
    "同时",
    "以及",
    "并且",
    "顺便",
)


@dataclass(frozen=True)
class RouterConfig:
    """Tunable Router V1 settings."""

    rule_router_enabled: bool = True
    classifier_enabled: bool = True
    llm_fallback_enabled: bool = True
    direct_route_threshold: float = 0.90
    margin_threshold: float = 0.30
    default_model_version: str = "router-v1"
    max_length: int = 128
    factor_keywords: tuple[str, ...] = field(default_factory=lambda: DEFAULT_FACTOR_KEYWORDS)
    chat_keywords: tuple[str, ...] = field(default_factory=lambda: DEFAULT_CHAT_KEYWORDS)
    multi_action_keywords: tuple[str, ...] = field(default_factory=lambda: DEFAULT_MULTI_ACTION_KEYWORDS)
    factor_research_recall_target: float = 0.97
    direct_route_error_target: float = 0.01

    def __post_init__(self) -> None:
        if not 0.0 <= self.direct_route_threshold <= 1.0:
            raise ValueError("direct_route_threshold must be between 0.0 and 1.0")
        if not 0.0 <= self.margin_threshold <= 1.0:
            raise ValueError("margin_threshold must be between 0.0 and 1.0")
        if self.max_length <= 0:
            raise ValueError("max_length must be positive")
