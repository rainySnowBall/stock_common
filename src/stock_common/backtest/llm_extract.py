"""LLM JSON extraction parser for natural-language strategy specs."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol

from stock_common.backtest.compiler import HeuristicStrategyParser
from stock_common.backtest.factor_catalog import factor_catalog_prompt
from stock_common.backtest.registry import (
    BENCHMARK_REGISTRY,
    UNIVERSE_ALIASES,
)
from stock_common.backtest.schemas import (
    BacktestSpec,
    DraftStrategySpec,
    FactorCondition,
    PortfolioSpec,
    RankingRule,
    RebalanceFrequency,
    RebalanceSpec,
    SelectionSpec,
    StrategySpec,
    StrategyStatus,
    UniverseSpec,
)
from stock_common.backtest.stock_resolver import StockSymbolResolver, normalize_ts_code
from stock_common.llm import (
    ChatLLMConfigError,
    ChatLLMError,
    ChatLLMSettings,
    ChatMessage,
    OpenAICompatibleChatClient,
)
from stock_common.model_selection.defaults import DEFAULT_MODEL_PROFILES
from stock_common.model_selection.schemas import ModelCapability, ModelFunction, ModelProfile


class StrategyExtractionClient(Protocol):
    """Minimal sync client contract used by the strategy extractor."""

    def complete_sync(self, messages: Iterable[ChatMessage]) -> str:
        """Return the assistant response text."""


@dataclass(frozen=True)
class LLMExtractStrategyParser:
    """Extract DraftStrategySpec JSON with an LLM, then coerce into fixed schemas."""

    client: StrategyExtractionClient | None = None
    model_profile: ModelProfile | None = None
    fallback_parser: HeuristicStrategyParser = HeuristicStrategyParser()
    stock_resolver: StockSymbolResolver = StockSymbolResolver()
    enabled: bool | None = None
    mode_env_name: str = "FACTOR_RESEARCH_PARSER_MODE"
    schema_version: str = "1.0.0"

    def parse(self, text: str) -> DraftStrategySpec:
        if not self._is_enabled():
            return self.fallback_parser.parse(text)

        try:
            content = self._client().complete_sync(_build_messages(text))
            payload = _extract_json_object(content)
            return _draft_from_payload(payload, self.schema_version, self.stock_resolver)
        except (ChatLLMConfigError, ChatLLMError, ValueError, TypeError, KeyError):
            return self.fallback_parser.parse(text)

    def for_model(self, model_profile: ModelProfile) -> "LLMExtractStrategyParser":
        return LLMExtractStrategyParser(
            client=self.client,
            model_profile=model_profile,
            fallback_parser=self.fallback_parser,
            stock_resolver=self.stock_resolver,
            enabled=self.enabled,
            mode_env_name=self.mode_env_name,
            schema_version=self.schema_version,
        )

    def _is_enabled(self) -> bool:
        if self.client is not None:
            return True
        if self.enabled is not None:
            return self.enabled
        mode = os.environ.get(self.mode_env_name, "").strip().lower()
        return mode in {"llm", "llm_extract", "extract", "json"}

    def _client(self) -> StrategyExtractionClient:
        if self.client is not None:
            return self.client
        model = self.model_profile or _default_model_profile()
        return OpenAICompatibleChatClient(ChatLLMSettings.from_model_profile(model))


def _default_model_profile() -> ModelProfile:
    for profile in DEFAULT_MODEL_PROFILES:
        if (
            ModelFunction.FACTOR_RESEARCH in profile.supported_functions
            and ModelCapability.JSON_OUTPUT in profile.capabilities
        ):
            return profile
    raise ChatLLMConfigError("no factor_research model profile with json_output capability")


def _build_messages(text: str) -> tuple[ChatMessage, ...]:
    return (
        ChatMessage(
            role="system",
            content=(
                "你是A股量化回测策略抽取器。只把用户自然语言抽取为 JSON，"
                "不得生成代码，不得编造未注册因子或规则。缺少会显著影响回测"
                "结果的参数时返回 needs_clarification。百分比必须转换为小数，"
                "例如 15% 输出 0.15。只输出一个 JSON 对象，不要 Markdown。"
            ),
        ),
        ChatMessage(
            role="user",
            content=_extraction_prompt(text),
        ),
    )


def _extraction_prompt(text: str) -> str:
    capabilities = {
        "allowed_universes": sorted(UNIVERSE_ALIASES),
        "allowed_single_stock_universe": "single_stock",
        "single_stock_code_format": "Tushare ts_code, e.g. 600118.SH / 000001.SZ / 430047.BJ",
        "allowed_benchmarks": sorted(BENCHMARK_REGISTRY),
        "allowed_operators": [">", ">=", "<", "<=", "=="],
        "allowed_ranking_directions": ["asc", "desc"],
        "allowed_rebalance_frequency": ["weekly", "monthly", "quarterly", "triggered"],
    }
    schema = {
        "status": "valid | needs_clarification | unsupported | invalid",
        "strategy": {
            "universe": {
                "name": "all_a_demo | csi300_demo | csi500_demo | single_stock",
                "symbols": ["600118.SH"],
                "display_name": "中国卫星",
            },
            "signals": [
                {"factor": "roe_ttm", "operator": ">", "value": 0.15, "unit": "decimal"}
            ],
            "entry_rules": [
                {"factor": "roe_ttm", "operator": ">", "value": 0.15, "unit": "decimal"},
                {"factor": "earnings_to_price", "operator": ">=", "value": 0.8, "unit": "percentile"},
            ],
            "exit_rules": [
                {"factor": "roe_ttm", "operator": "<", "value": 0.10, "unit": "decimal"},
                {"factor": "earnings_to_price", "operator": "<=", "value": 0.2, "unit": "percentile"},
            ],
            "ranking": [
                {"factor": "earnings_to_price", "direction": "desc", "weight": 1.0}
            ],
            "selection": {"count": 20},
            "rebalance": {"frequency": "optional: weekly | monthly | quarterly | triggered"},
            "backtest": {"lookback_years": 5, "benchmark": "000300.SH"},
        },
        "unresolved_fields": [
            {"path": "selection.count", "reason": "缺少选股数量", "candidates": [10, 20, 30]}
        ],
        "assumptions": [],
        "field_confidence": {"ranking[0].factor": 0.95},
    }
    return (
        "能力注册表：\n"
        f"{json.dumps(capabilities, ensure_ascii=False, indent=2)}\n\n"
        "真实因子映射表：\n"
        f"{factor_catalog_prompt()}\n\n"
        "输出 JSON 形状示例：\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        "规则：\n"
        "- factor 必须是“真实因子映射表”里的 factor_name；如果用户明确写了表中的 factor_name，直接使用该名称。\n"
        "- 低 PE / 低市盈率 / 便宜估值优先映射为 earnings_to_price，排序 direction 用 desc。\n"
        "- 个股回测支持 universe.name=single_stock，并在 universe.symbols 填 Tushare ts_code；如果只有中文股票名，可以根据已知 A 股简称/股票代码解析，无法确定时才把 universe.name 放入 unresolved_fields。\n"
        "- PE 前20%/后20%这类条件是历史分位条件，不要编造 pe_percentile 因子；用 earnings_to_price，并设置 unit=percentile。由于 earnings_to_price 是 PE 倒数，PE 后20%买入 => earnings_to_price >= 0.8 分位；PE 前20%卖出 => earnings_to_price <= 0.2 分位。\n"
        "- 低 PB / 高账面市值比优先映射为 book_to_market，排序 direction 用 desc。\n"
        "- 小市值优先映射为 size，排序 direction 用 desc；大市值用 size asc。\n"
        "- 动量或过去 N 日收益按用户窗口优先映射 return_5d、return_21d、return_63d、return_126d 或 return_252d。\n"
        "- 如果用户只说“高质量”“低估值”等宽泛风格且映射表有多个合理候选，返回 needs_clarification 并给出 candidates。\n"
        "- 普通筛选条件放 signals。\n"
        "- 明确包含“买入/建仓/入场”的条件放 entry_rules。\n"
        "- 明确包含“卖出/清仓/退出/止盈/止损”的条件放 exit_rules。\n"
        "- 如果没有显式买卖规则，entry_rules 和 exit_rules 返回空数组。\n"
        "- rebalance.frequency 是可选字段；如果用户说“达到/触发/满足条件/一旦有信号就买卖”，或存在明确 entry_rules/exit_rules 且没有固定频率，使用 triggered；如果是股票池排序选股且没有固定频率，默认 monthly 并在 assumptions 说明。\n"
        "- 排名方向不明确时按用户语义推断，并在 assumptions 说明。\n"
        "- 百分比阈值 value 用小数表示，并在条件里加 unit=decimal；RSI 等原始数值用 unit=raw。\n"
        "- 股票池/个股、回测区间、选股数量缺失时返回 needs_clarification；调仓频率缺失时不要追问，按上面的默认规则处理；single_stock 默认 selection.count=1，不需要追问选股数量。\n"
        "- 不要编造不在映射表中的 factor_name。\n\n"
        f"用户输入：{text}"
    )


def _extract_json_object(content: str) -> Mapping[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("LLM extraction response did not contain a JSON object")
        payload = json.loads(match.group(0))
    if not isinstance(payload, Mapping):
        raise ValueError("LLM extraction response was not a JSON object")
    return payload


def _draft_from_payload(
    payload: Mapping[str, Any],
    schema_version: str,
    stock_resolver: StockSymbolResolver,
) -> DraftStrategySpec:
    status = StrategyStatus(str(payload.get("status", StrategyStatus.INVALID.value)))
    unresolved_fields = _unresolved_fields(payload.get("unresolved_fields"))
    assumptions = tuple(str(item) for item in _as_list(payload.get("assumptions")))
    field_confidence = _field_confidence(payload.get("field_confidence"))

    if status is not StrategyStatus.VALID:
        return DraftStrategySpec(
            schema_version=schema_version,
            status=status,
            unresolved_fields=unresolved_fields,
            assumptions=assumptions,
            field_confidence=field_confidence,
        )

    strategy_payload = payload.get("strategy")
    if not isinstance(strategy_payload, Mapping):
        return _missing_strategy_draft(schema_version, assumptions, field_confidence)

    missing = _missing_required_fields(strategy_payload)
    if missing:
        return DraftStrategySpec(
            schema_version=schema_version,
            status=StrategyStatus.NEEDS_CLARIFICATION,
            unresolved_fields=tuple(
                {"path": path, "reason": "LLM 抽取结果缺少必填策略参数。"}
                for path in missing
            ),
            assumptions=assumptions,
            field_confidence=field_confidence,
        )

    universe = _universe_from_payload(
        _mapping(strategy_payload["universe"]),
        stock_resolver,
    )
    signals = tuple(_condition(item) for item in _as_list(strategy_payload.get("signals")))
    ranking = tuple(_ranking_rule(item) for item in _as_list(strategy_payload.get("ranking")))
    entry_rules = tuple(
        _condition(item) for item in _as_list(strategy_payload.get("entry_rules"))
    )
    exit_rules = tuple(
        _condition(item) for item in _as_list(strategy_payload.get("exit_rules"))
    )
    rebalance_frequency = _rebalance_frequency_from_payload(
        strategy_payload,
        has_signal_rules=bool(entry_rules or exit_rules),
    )
    rebalance_assumption = _rebalance_frequency_assumption(
        strategy_payload,
        rebalance_frequency,
    )
    if rebalance_assumption and rebalance_assumption not in assumptions:
        assumptions = (*assumptions, rebalance_assumption)
    selection_count = _selection_count(strategy_payload, universe)
    strategy = StrategySpec(
        schema_version=schema_version,
        universe=universe,
        signals=signals,
        ranking=ranking,
        selection=SelectionSpec(count=selection_count),
        portfolio=PortfolioSpec(max_position_weight=round(1 / selection_count, 6)),
        rebalance=RebalanceSpec(frequency=rebalance_frequency),
        backtest=BacktestSpec(
            lookback_years=int(_mapping(strategy_payload["backtest"]).get("lookback_years")),
            benchmark=str(_mapping(strategy_payload["backtest"]).get("benchmark", "000300.SH")),
        ),
        entry_rules=entry_rules,
        exit_rules=exit_rules,
        assumptions=assumptions,
    )
    return DraftStrategySpec(
        schema_version=schema_version,
        status=StrategyStatus.VALID,
        strategy=strategy,
        assumptions=assumptions,
        field_confidence=field_confidence,
    )


def _missing_strategy_draft(
    schema_version: str,
    assumptions: tuple[str, ...],
    field_confidence: dict[str, float],
) -> DraftStrategySpec:
    return DraftStrategySpec(
        schema_version=schema_version,
        status=StrategyStatus.NEEDS_CLARIFICATION,
        unresolved_fields=(
            {
                "path": "strategy",
                "reason": "LLM 抽取结果没有返回完整策略结构。",
            },
        ),
        assumptions=assumptions,
        field_confidence=field_confidence,
    )


def _missing_required_fields(strategy_payload: Mapping[str, Any]) -> list[str]:
    universe_payload = strategy_payload.get("universe")
    has_single_stock_symbols = (
        isinstance(universe_payload, Mapping)
        and bool(_coerce_symbols(universe_payload.get("symbols")))
    )
    required_paths = [
        ("universe.name", ("universe", "name")),
        ("ranking", ("ranking",)),
        ("selection.count", ("selection", "count")),
        ("backtest.lookback_years", ("backtest", "lookback_years")),
    ]
    missing = []
    for path, keys in required_paths:
        if path == "universe.name" and has_single_stock_symbols:
            continue
        if path == "selection.count" and has_single_stock_symbols:
            continue
        if path == "ranking" and has_single_stock_symbols:
            continue
        value: Any = strategy_payload
        for key in keys:
            if not isinstance(value, Mapping) or key not in value or value[key] in (None, ""):
                missing.append(path)
                break
            value = value[key]
        if path == "ranking" and isinstance(value, list) and not value:
            missing.append(path)
    return missing


def _universe_from_payload(
    item: Mapping[str, Any],
    stock_resolver: StockSymbolResolver,
) -> UniverseSpec:
    raw_name = str(item.get("name") or "").strip()
    symbols = _coerce_symbols(item.get("symbols"))
    display_name = str(item.get("display_name") or "").strip() or None
    if not symbols and raw_name and raw_name not in UNIVERSE_ALIASES and raw_name != "single_stock":
        resolved = stock_resolver.resolve(raw_name)
        if resolved is not None:
            symbols = (resolved.ts_code,)
            display_name = display_name or resolved.name or resolved.symbol
    if symbols:
        return UniverseSpec(
            name="single_stock",
            symbols=symbols,
            display_name=display_name,
            filters=(
                {
                    "field": "ts_code",
                    "operator": "in",
                    "value": list(symbols),
                    "source": "llm_extract",
                },
            ),
        )
    return UniverseSpec(name=raw_name or "all_a_demo", display_name=display_name)


def _coerce_symbols(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    raw_items = value if isinstance(value, list) else [value]
    symbols = []
    for raw_item in raw_items:
        symbol = normalize_ts_code(str(raw_item))
        if symbol is not None:
            symbols.append(symbol)
    return tuple(dict.fromkeys(symbols))


def _selection_count(strategy_payload: Mapping[str, Any], universe: UniverseSpec) -> int:
    selection_payload = strategy_payload.get("selection")
    if isinstance(selection_payload, Mapping) and selection_payload.get("count") not in (None, ""):
        return int(selection_payload.get("count"))
    if universe.symbols:
        return len(universe.symbols)
    raise ValueError("selection.count is required")


def _rebalance_frequency_from_payload(
    strategy_payload: Mapping[str, Any],
    *,
    has_signal_rules: bool,
) -> RebalanceFrequency:
    rebalance_payload = strategy_payload.get("rebalance")
    if isinstance(rebalance_payload, Mapping):
        raw_frequency = rebalance_payload.get("frequency")
        if raw_frequency not in (None, ""):
            return RebalanceFrequency(str(raw_frequency))

    if has_signal_rules:
        return RebalanceFrequency.TRIGGERED
    return RebalanceFrequency.MONTHLY


def _rebalance_frequency_assumption(
    strategy_payload: Mapping[str, Any],
    frequency: RebalanceFrequency,
) -> str:
    rebalance_payload = strategy_payload.get("rebalance")
    if isinstance(rebalance_payload, Mapping) and rebalance_payload.get("frequency") not in (None, ""):
        return ""
    if frequency is RebalanceFrequency.TRIGGERED:
        return "未明确固定调仓频率；由于存在入场/出场规则，默认使用事件触发调仓。"
    return "未明确调仓频率；默认使用每月调仓。"


def _condition(value: object) -> FactorCondition:
    item = _mapping(value)
    return FactorCondition(
        factor=str(item["factor"]),
        operator=str(item["operator"]),
        value=float(item["value"]),
        unit=str(item.get("unit", "raw")),
    )


def _ranking_rule(value: object) -> RankingRule:
    item = _mapping(value)
    return RankingRule(
        factor=str(item["factor"]),
        direction=str(item["direction"]),
        weight=float(item.get("weight", 1.0)),
    )


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("expected JSON object")
    return value


def _as_list(value: object) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("expected JSON array")
    return value


def _unresolved_fields(value: object) -> tuple[dict[str, Any], ...]:
    fields = []
    for item in _as_list(value):
        mapping = _mapping(item)
        fields.append(
            {
                "path": str(mapping.get("path", "")),
                "reason": str(mapping.get("reason", "")),
                "candidates": mapping.get("candidates", []),
            }
        )
    return tuple(fields)


def _field_confidence(value: object) -> dict[str, float]:
    if value is None:
        return {}
    mapping = _mapping(value)
    return {str(key): float(item) for key, item in mapping.items()}
