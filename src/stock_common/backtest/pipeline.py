"""End-to-end natural-language factor strategy pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from stock_common.backtest.compiler import (
    HeuristicStrategyParser,
    StrategyCompiler,
    StrategyValidator,
)
from stock_common.backtest.charts import ChartEngine
from stock_common.backtest.engine import BacktestEngine
from stock_common.backtest.factor_catalog import factor_display_name
from stock_common.backtest.llm_extract import LLMExtractStrategyParser
from stock_common.backtest.metrics import MetricsEngine
from stock_common.backtest.registry import FACTOR_REGISTRY
from stock_common.backtest.schemas import (
    BacktestFacts,
    ChartBundle,
    DataStatus,
    DraftStrategySpec,
    ExecutionPlan,
    FactorCondition,
    MetricsResult,
    PipelineResult,
    RankingRule,
    StrategyStatus,
    ValidationIssue,
)


class StrategyTextParser(Protocol):
    def parse(self, text: str) -> DraftStrategySpec:
        ...


class BacktestRunner(Protocol):
    def run(self, plan: ExecutionPlan) -> BacktestFacts:
        ...


@dataclass(frozen=True)
class FactorResearchPipeline:
    """Compile user text into a deterministic strategy plan and configured backtest."""

    parser: StrategyTextParser = LLMExtractStrategyParser()
    validator: StrategyValidator = StrategyValidator()
    compiler: StrategyCompiler = StrategyCompiler()
    engine: BacktestRunner = BacktestEngine()
    metrics_engine: MetricsEngine = MetricsEngine()
    chart_engine: ChartEngine = ChartEngine()

    def compile(self, text: str) -> PipelineResult:
        """Run parser, validator, normalizer, and compiler without simulation."""

        draft = self.parser.parse(text)
        validation = self.validator.validate(draft)
        if not validation.is_valid:
            return PipelineResult(
                status=validation.status,
                content=_render_validation_message(validation.issues, draft),
                draft=draft,
                validation=validation,
            )

        assert validation.spec is not None
        plan = self.compiler.compile(validation.spec)
        return PipelineResult(
            status=StrategyStatus.VALID,
            content=_render_compile_message(plan),
            draft=draft,
            validation=validation,
            plan=plan,
        )

    def run(self, text: str) -> PipelineResult:
        """Run the full P0 pipeline from natural language to BacktestFacts."""

        compiled = self.compile(text)
        if compiled.plan is None:
            return compiled

        facts = self.engine.run(compiled.plan)
        if facts.status is not DataStatus.SUCCEEDED:
            return PipelineResult(
                status=StrategyStatus.INVALID,
                content=_render_backtest_failure(facts),
                draft=compiled.draft,
                validation=compiled.validation,
                plan=compiled.plan,
                facts=facts,
            )

        metrics = self.metrics_engine.calculate(facts)
        charts = self.chart_engine.build(metrics)
        return PipelineResult(
            status=StrategyStatus.VALID,
            content=_render_success_message(compiled.plan, facts, metrics, charts),
            draft=compiled.draft,
            validation=compiled.validation,
            plan=compiled.plan,
            facts=facts,
            metrics=metrics,
            charts=charts,
        )


def _render_validation_message(
    issues: tuple[ValidationIssue, ...],
    draft: DraftStrategySpec | None = None,
) -> str:
    if not issues:
        return "策略没有通过校验，但未返回具体问题。"

    lines = ["需要先补充或修正以下策略参数："]
    for issue in issues:
        hint = _clarification_hint(issue.path, draft)
        suffix = f" 建议：{hint}" if hint else ""
        lines.append(f"- `{issue.path}`：{issue.message}{suffix}")

    default_sentence = _default_clarification_sentence(issues)
    if default_sentence:
        lines.append("")
        lines.append(default_sentence)
        lines.append("你也可以只补充其中几个参数，系统会继续提示当次输入里剩余的不确定项。")
    return "\n".join(lines)


def _clarification_hint(path: str, draft: DraftStrategySpec | None) -> str:
    candidates = _candidate_text(path, draft)
    recommended = {
        "backtest.lookback_years": "如果没有特定周期，先用过去 5 年。",
        "selection.count": "如果只是快速验证策略，先选 20 只。",
        "rebalance.frequency": "如果没有交易频率偏好，先用每月调仓。",
        "universe.name": "如果没有指定指数范围，先用全 A；如果想看大盘风格，可用沪深300。",
    }.get(path, "")
    if candidates and recommended:
        return f"可选 {candidates}；{recommended}"
    return candidates or recommended


def _candidate_text(path: str, draft: DraftStrategySpec | None) -> str:
    if draft is None:
        return ""
    for item in draft.unresolved_fields:
        if item.get("path") != path:
            continue
        candidates = item.get("candidates")
        if not isinstance(candidates, (list, tuple)) or not candidates:
            return ""
        labels = [_candidate_label(path, candidate) for candidate in candidates]
        return "、".join(label for label in labels if label)
    return ""


def _candidate_label(path: str, candidate: object) -> str:
    if path == "backtest.lookback_years":
        return f"过去 {candidate} 年"
    if path == "selection.count":
        return f"{candidate} 只"
    if path == "rebalance.frequency":
        return {
            "weekly": "每周",
            "monthly": "每月",
            "quarterly": "每季度",
            "triggered": "触发式",
        }.get(str(candidate), str(candidate))
    if path == "universe.name":
        return {
            "csi300_demo": "沪深300",
            "csi500_demo": "中证500",
            "all_a_demo": "全A",
        }.get(str(candidate), str(candidate))
    return str(candidate)


def _default_clarification_sentence(issues: tuple[ValidationIssue, ...]) -> str:
    paths = {issue.path for issue in issues}
    defaults = []
    if "universe.name" in paths:
        defaults.append("全 A")
    if "backtest.lookback_years" in paths:
        defaults.append("过去 5 年")
    if "selection.count" in paths:
        defaults.append("选 20 只")
    if "rebalance.frequency" in paths:
        defaults.append("每月调仓")
    if not defaults:
        return ""
    return "如果你不确定，可以参考默认组合：" + "、".join(defaults) + "。"


def _render_compile_message(plan: ExecutionPlan) -> str:
    return "\n".join(
        (
            "策略已编译为确定性执行计划，尚未运行回测。",
            f"策略确认：{describe_plan(plan)}",
            f"执行计划：`{plan.plan_id}`，策略 Hash `{plan.strategy_hash}`。",
        )
    )


def _render_success_message(
    plan: ExecutionPlan,
    facts: BacktestFacts,
    metrics: MetricsResult,
    charts: ChartBundle,
) -> str:
    summary = _metrics_summary(metrics)
    chart_count = sum(1 for chart in charts.charts if chart.status == "ready")
    mode_label = _backtest_mode_label(facts)
    lines = [
        f"策略已编译，并完成{mode_label}回测、指标计算和图表数据生成。",
        f"策略确认：{describe_plan(plan)}",
        f"执行计划：`{plan.plan_id}`，策略 Hash `{plan.strategy_hash}`。",
    ]
    if summary:
        lines.append(
            "指标摘要："
            f"区间 {summary['start_date']} 至 {summary['end_date']}，"
            f"年化收益 {summary['annual_return']}，"
            f"年化超额 {summary['annual_excess_return']}，"
            f"Sharpe {summary['sharpe_ratio']}，"
            f"最大回撤 {summary['max_drawdown']}，"
            f"年换手率 {summary['annual_turnover']}。"
        )
    lines.append(f"图表数据：已生成 {chart_count} 张图，包括净值、回撤、月度收益、年度收益、滚动收益、滚动 Sharpe、换手和成本。")
    note = _backtest_note(facts)
    if note:
        lines.append(note)
    warning_note = _warning_note(facts)
    if warning_note:
        lines.append(warning_note)
    return "\n".join(lines)


def _render_backtest_failure(facts: BacktestFacts) -> str:
    warning_messages = [
        str(item.get("message", item.get("code", ""))) for item in facts.warnings
    ]
    detail = "；".join(message for message in warning_messages if message)
    if detail:
        return f"策略已编译，但回测运行失败：{detail}"
    return "策略已编译，但回测运行失败。"


def describe_plan(plan: ExecutionPlan) -> str:
    """Render a fixed-template strategy confirmation sentence."""

    filter_text = _describe_filters(plan.filters)
    entry_text = _describe_trade_rules("入场", plan.entry_rules)
    exit_text = _describe_trade_rules("出场", plan.exit_rules)
    ranking_text = _describe_ranking(plan.ranking)
    frequency = {
        "weekly": "每周",
        "monthly": "每月",
        "quarterly": "每季",
        "triggered": "触发式",
    }[plan.rebalance.frequency.value]
    return (
        f"在 `{_universe_label(plan)}` 股票池中{filter_text}"
        f"{entry_text}"
        f"{exit_text}"
        f"{ranking_text}，选择前 {plan.selection.count} 只，等权持有；"
        f"{frequency}调仓，{_trade_timing_text(plan)}；"
        f"回测过去 {plan.backtest.lookback_years} 年，基准 `{plan.backtest.benchmark}`。"
    )


def _universe_label(plan: ExecutionPlan) -> str:
    if plan.universe.symbols:
        symbols = "、".join(plan.universe.symbols)
        if plan.universe.display_name:
            return f"{plan.universe.display_name}（{symbols}）"
        return symbols
    return plan.universe.name


def _describe_filters(filters: tuple[FactorCondition, ...]) -> str:
    if not filters:
        return "，不设置额外因子过滤条件，"
    parts = [
        f"{_factor_name(item.factor)} {_operator_text(item.operator.value)} {_format_factor_value(item)}"
        for item in filters
    ]
    return "，保留 " + " 且 ".join(parts) + " 的股票，"


def _describe_trade_rules(label: str, rules: tuple[FactorCondition, ...]) -> str:
    if not rules:
        return ""
    parts = [
        f"{_factor_name(item.factor)} {_operator_text(item.operator.value)} {_format_factor_value(item)}"
        for item in rules
    ]
    return f"{label}规则：" + " 且 ".join(parts) + "；"


def _describe_ranking(ranking: tuple[RankingRule, ...]) -> str:
    if not ranking:
        return "不额外排序"
    parts = []
    for item in ranking:
        direction = "从小到大" if item.direction.value == "asc" else "从大到小"
        parts.append(f"{_factor_name(item.factor)}{direction}")
    return "按" + "、".join(parts) + "排序"


def _trade_timing_text(plan: ExecutionPlan) -> str:
    if plan.rebalance.signal_at == "close" and plan.rebalance.trade_at == "next_open":
        return "收盘生成信号、下一交易日开盘成交"
    return f"{plan.rebalance.signal_at} 生成信号、{plan.rebalance.trade_at} 成交"


def _metrics_summary(metrics: MetricsResult) -> dict[str, str] | None:
    performance = metrics.summary.get("performance", {})
    risk = metrics.summary.get("risk", {})
    trading = metrics.summary.get("trading", {})
    facts_metadata = metrics.metadata.get("facts_metadata", {})
    if not performance:
        return None
    return {
        "start_date": str(facts_metadata.get("start_date", "")),
        "end_date": str(facts_metadata.get("end_date", "")),
        "annual_return": _format_optional_percent(performance.get("annual_return")),
        "annual_excess_return": _format_optional_percent(performance.get("annual_excess_return")),
        "sharpe_ratio": _format_number(risk.get("sharpe_ratio")),
        "max_drawdown": _format_optional_percent(risk.get("max_drawdown")),
        "annual_turnover": _format_optional_percent(trading.get("annual_turnover")),
    }


def _backtest_mode_label(facts: BacktestFacts) -> str:
    return "demo " if _is_demo_backtest(facts) else "真实数据"


def _backtest_note(facts: BacktestFacts) -> str:
    if _is_demo_backtest(facts):
        return "提示：当前 P0 引擎使用确定性 demo 数据，用于验证组件链路，不代表真实市场结果。"
    data_source = facts.data_quality.get("data_source") or facts.metadata.get("data_version")
    if data_source:
        return f"数据源：{data_source}。"
    return ""


def _warning_note(facts: BacktestFacts) -> str:
    skipped_codes = {"DEMO_DATA", "REAL_DATA_SIMPLE_ENGINE"}
    messages = [
        str(item.get("message", item.get("code", "")))
        for item in facts.warnings
        if item.get("code") not in skipped_codes
    ]
    messages = [message for message in messages if message]
    if not messages:
        return ""
    return "注意：" + "；".join(messages[:3])


def _is_demo_backtest(facts: BacktestFacts) -> bool:
    engine_version = str(facts.metadata.get("engine_version", "")).lower()
    data_source = str(facts.data_quality.get("data_source", "")).lower()
    data_version = str(facts.metadata.get("data_version", "")).lower()
    return "demo" in engine_version or "demo" in data_source or "demo" in data_version


def _factor_name(factor: str) -> str:
    return {
        "roe_ttm": "ROE_TTM",
        "pe_ttm": "PE_TTM",
        "earnings_to_price": "E/P（PE倒数）",
        "pb": "PB",
        "market_cap": "总市值",
        "momentum_20d": "20 日动量",
    }.get(factor, factor_display_name(factor))


def _operator_text(operator: str) -> str:
    return {
        ">": "大于",
        ">=": "大于等于",
        "<": "小于",
        "<=": "小于等于",
        "==": "等于",
    }.get(operator, operator)


def _format_factor_value(condition: FactorCondition) -> str:
    if condition.unit == "percentile":
        return f"{condition.value * 100:g}%分位"
    definition = FACTOR_REGISTRY.get(condition.factor)
    if definition is not None and definition.unit == "decimal":
        return _format_percent(condition.value)
    if condition.unit in {"decimal", "percent"}:
        return _format_percent(condition.value)
    return f"{condition.value:g}"


def _format_percent(value: float) -> str:
    percent = f"{value * 100:.2f}".rstrip("0").rstrip(".")
    return f"{percent}%"


def _format_optional_percent(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "不可计算"
    return _format_percent(float(value))


def _format_number(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "不可计算"
    return f"{float(value):.2f}"
