"""Fixed registries for the P0 factor-backtest DSL."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FactorDefinition:
    name: str
    aliases: tuple[str, ...]
    dtype: str
    unit: str
    direction: str
    available_from: str
    version: str


FACTOR_REGISTRY: dict[str, FactorDefinition] = {
    "roe_ttm": FactorDefinition(
        name="roe_ttm",
        aliases=("roe", "净资产收益率", "股东权益回报率", "高roe", "低roe"),
        dtype="ratio",
        unit="decimal",
        direction="higher_is_better",
        available_from="announce_date",
        version="1.0.0",
    ),
    "pe_ttm": FactorDefinition(
        name="pe_ttm",
        aliases=("pe", "市盈率", "低pe", "高pe"),
        dtype="ratio",
        unit="multiple",
        direction="lower_is_better",
        available_from="announce_date",
        version="1.0.0",
    ),
    "pb": FactorDefinition(
        name="pb",
        aliases=("pb", "市净率", "低pb", "高pb"),
        dtype="ratio",
        unit="multiple",
        direction="lower_is_better",
        available_from="announce_date",
        version="1.0.0",
    ),
    "market_cap": FactorDefinition(
        name="market_cap",
        aliases=("市值", "总市值", "小市值", "大市值", "market cap"),
        dtype="currency",
        unit="cny",
        direction="lower_is_better",
        available_from="trade_date",
        version="1.0.0",
    ),
    "momentum_20d": FactorDefinition(
        name="momentum_20d",
        aliases=("动量", "momentum", "过去20日收益", "20日动量"),
        dtype="ratio",
        unit="decimal",
        direction="higher_is_better",
        available_from="trade_date",
        version="1.0.0",
    ),
}


BENCHMARK_REGISTRY: dict[str, str] = {
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
    "000852.SH": "中证1000",
}


UNIVERSE_ALIASES: dict[str, tuple[str, ...]] = {
    "csi300_demo": ("沪深300", "hs300", "csi300", "000300"),
    "csi500_demo": ("中证500", "zz500", "csi500", "000905"),
    "all_a_demo": ("全a", "全部a股", "a股", "cn_a"),
}


REGISTRY_VERSIONS: dict[str, str] = {
    "strategy_schema_version": "1.0.0",
    "factor_registry_version": "1.0.0",
    "execution_policy_version": "1.0.0",
    "data_version": "demo-cn-a-v1",
    "engine_version": "demo-backtest-engine-v1.0.0",
    "metrics_version": "backtest-metrics-v1.0.0",
    "evaluation_profile_version": "cn-long-only-demo-v1.0.0",
    "chart_schema_version": "chart-bundle-v1.0.0",
    "interpreter_prompt_version": "deterministic-template-v1.0.0",
}


def find_factor(text: str) -> str | None:
    normalized = text.lower()
    matches = []
    for factor_name, definition in FACTOR_REGISTRY.items():
        for alias in definition.aliases:
            if alias.lower() in normalized:
                matches.append((normalized.index(alias.lower()), factor_name))
                break

    if not matches:
        return None
    return sorted(matches, key=lambda item: item[0])[0][1]


def find_universe(text: str) -> str | None:
    normalized = text.lower().replace(" ", "")
    for universe_name, aliases in UNIVERSE_ALIASES.items():
        if any(alias.lower().replace(" ", "") in normalized for alias in aliases):
            return universe_name
    return None
