"""Natural-language strategy parsing, validation, normalization, and compilation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from stock_common.backtest.factor_catalog import (
    factor_aliases,
    factor_exists,
    search_factor_matches,
)
from stock_common.backtest.registry import (
    BENCHMARK_REGISTRY,
    FACTOR_REGISTRY,
    REGISTRY_VERSIONS,
    find_universe,
)
from stock_common.backtest.schemas import (
    BacktestSpec,
    DraftStrategySpec,
    ExecutionPlan,
    FactorCondition,
    Operator,
    PortfolioSpec,
    RankingRule,
    RebalanceFrequency,
    RebalanceSpec,
    SelectionSpec,
    SortDirection,
    StrategySpec,
    StrategyStatus,
    UniverseSpec,
    ValidationIssue,
    ValidationResult,
    to_serializable,
)
from stock_common.backtest.stock_resolver import ResolvedStock, StockSymbolResolver


_CN_NUMBERS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

_BUY_KEYWORDS = ("买入", "买进", "建仓", "开仓", "入场")
_SELL_KEYWORDS = ("卖出", "卖掉", "清仓", "平仓", "退出", "离场", "止盈", "止损")
_CLAUSE_SPLIT_RE = re.compile(r"[，,。；;]|(?:然后)|(?:同时)|(?:并且)|(?:以及)|(?:且)")


@dataclass(frozen=True)
class HeuristicStrategyParser:
    """Parse a narrow P0 set of fixed factor strategies without executing code."""

    schema_version: str = "1.0.0"
    stock_resolver: StockSymbolResolver = StockSymbolResolver()

    def parse(self, text: str) -> DraftStrategySpec:
        source = text.strip()
        unresolved = []
        assumptions = []
        ranking_assumed = False

        mentioned_factors = _find_mentioned_factors(source)
        if not mentioned_factors:
            return DraftStrategySpec(
                schema_version=self.schema_version,
                status=StrategyStatus.UNSUPPORTED,
                unresolved_fields=(
                    {
                        "path": "ranking[0].factor",
                        "reason": "未识别到 factor_map 中的 factor_name、中文映射或因子描述关键词。",
                    },
                ),
            )
        primary_factor = mentioned_factors[0]

        lookback_years = _parse_lookback_years(source)
        if lookback_years is None:
            unresolved.append(
                {
                    "path": "backtest.lookback_years",
                    "reason": "缺少回测区间，例如“过去五年”或“过去10年”。",
                    "candidates": [3, 5, 10],
                }
            )

        universe_name = find_universe(source)
        stock = None if universe_name is not None else self.stock_resolver.resolve(source)
        is_single_stock = stock is not None

        entry_rules = tuple(_parse_action_factor_rules(source, _BUY_KEYWORDS))
        exit_rules = tuple(_parse_action_factor_rules(source, _SELL_KEYWORDS))
        explicit_rule_keys = {
            _condition_key(condition) for condition in (*entry_rules, *exit_rules)
        }
        filters = tuple(_parse_all_factor_filters(source, exclude_keys=explicit_rule_keys))
        ranking = tuple(_parse_ranking_rules(source, filters))
        if is_single_stock:
            ranking = ()
        if not ranking and not is_single_stock:
            default_direction = _factor_default_direction(primary_factor)
            fallback_direction = (
                SortDirection.DESC if default_direction == "higher_is_better" else SortDirection.ASC
            )
            ranking_assumed = True
            assumptions.append(
                f"未明确排名方向，按因子注册表默认方向使用 {primary_factor} {fallback_direction.value}。"
            )
            ranking = (RankingRule(factor=primary_factor, direction=fallback_direction),)

        count = _parse_selection_count(source)
        if is_single_stock:
            count = 1
        if count is None:
            unresolved.append(
                {
                    "path": "selection.count",
                    "reason": "缺少选股数量，例如“前20只”或“选30只”。",
                    "candidates": [10, 20, 30],
                }
            )

        frequency = _parse_frequency(source)
        frequency_confidence = 0.95
        if frequency is None and (entry_rules or exit_rules):
            frequency = RebalanceFrequency.TRIGGERED
            frequency_confidence = 0.72
            assumptions.append("未明确固定调仓频率；由于存在入场/出场规则，默认使用事件触发调仓。")
        if frequency is None:
            frequency = RebalanceFrequency.MONTHLY
            frequency_confidence = 0.70
            assumptions.append("未明确调仓频率；默认使用每月调仓。")

        if universe_name is None and stock is None:
            unresolved.append(
                {
                    "path": "universe.name",
                    "reason": "缺少股票池或个股名称/代码，例如“沪深300”“全A”或“600118.SH”。",
                    "candidates": ["csi300_demo", "csi500_demo", "all_a_demo", "600118.SH"],
                }
            )

        if unresolved:
            return DraftStrategySpec(
                schema_version=self.schema_version,
                status=StrategyStatus.NEEDS_CLARIFICATION,
                unresolved_fields=tuple(unresolved),
                assumptions=tuple(assumptions),
                field_confidence={
                    "ranking[0].factor": 0.78 if ranking_assumed else 0.96,
                    "ranking[0].direction": 0.72 if ranking_assumed else 0.94,
                    "rebalance.frequency": frequency_confidence,
                },
            )

        spec = StrategySpec(
            schema_version=self.schema_version,
            universe=_universe_spec(universe_name, stock),
            signals=filters,
            ranking=ranking,
            selection=SelectionSpec(count=count or 20),
            portfolio=PortfolioSpec(max_position_weight=round(1 / (count or 20), 6)),
            rebalance=RebalanceSpec(frequency=frequency),
            backtest=BacktestSpec(
                lookback_years=lookback_years or 5,
                benchmark=_parse_benchmark(source),
            ),
            entry_rules=entry_rules,
            exit_rules=exit_rules,
            assumptions=tuple(assumptions),
        )
        return DraftStrategySpec(
            schema_version=self.schema_version,
            status=StrategyStatus.VALID,
            strategy=spec,
            assumptions=tuple(assumptions),
            field_confidence={
                "ranking[0].factor": 0.78 if ranking_assumed else 0.96,
                "ranking[0].direction": 0.72 if ranking_assumed else 0.94,
                "selection.count": 0.95,
                "rebalance.frequency": frequency_confidence,
                "backtest.lookback_years": 0.95,
            },
        )


@dataclass(frozen=True)
class StrategyValidator:
    """Validate StrategySpec against fixed registries and P0 constraints."""

    def validate(self, draft: DraftStrategySpec) -> ValidationResult:
        if draft.status is not StrategyStatus.VALID or draft.strategy is None:
            return ValidationResult(
                status=draft.status,
                issues=tuple(
                    ValidationIssue(
                        path=str(item.get("path", "")),
                        code=draft.status.value,
                        message=str(item.get("reason", "")),
                    )
                    for item in draft.unresolved_fields
                ),
            )

        spec = draft.strategy
        issues: list[ValidationIssue] = []
        for path, conditions in (
            ("signals.factor", spec.signals),
            ("entry_rules.factor", spec.entry_rules),
            ("exit_rules.factor", spec.exit_rules),
        ):
            issues.extend(_validate_factor_conditions(path, conditions))
        for ranking in spec.ranking:
            if not _factor_is_supported(ranking.factor):
                issues.append(
                    ValidationIssue(
                        path="ranking.factor",
                        code="unsupported_factor",
                        message=f"未注册因子：{ranking.factor}",
                    )
                )
            if ranking.weight <= 0:
                issues.append(
                    ValidationIssue(
                        path="ranking.weight",
                        code="invalid_weight",
                        message="排名权重必须大于 0。",
                    )
                )
        if spec.selection.count <= 0:
            issues.append(
                ValidationIssue(
                    path="selection.count",
                    code="invalid_count",
                    message="选股数量必须大于 0。",
                )
            )
        if spec.backtest.lookback_years < 1 or spec.backtest.lookback_years > 30:
            issues.append(
                ValidationIssue(
                    path="backtest.lookback_years",
                    code="invalid_lookback",
                    message="回测年限必须在 1 到 30 年之间。",
                )
            )
        if spec.backtest.benchmark not in BENCHMARK_REGISTRY:
            issues.append(
                ValidationIssue(
                    path="backtest.benchmark",
                    code="unsupported_benchmark",
                    message=f"未注册基准：{spec.backtest.benchmark}",
                )
            )

        contradictory = _find_contradictory_filters(spec.signals)
        issues.extend(contradictory)
        if issues:
            return ValidationResult(status=StrategyStatus.INVALID, issues=tuple(issues))
        return ValidationResult(status=StrategyStatus.VALID, spec=spec)


@dataclass(frozen=True)
class StrategyNormalizer:
    """Normalize equivalent StrategySpec objects into a stable representation."""

    def normalize(self, spec: StrategySpec) -> StrategySpec:
        signals = tuple(
            sorted(
                spec.signals,
                key=lambda item: (item.factor, item.operator.value, item.value, item.unit),
            )
        )
        entry_rules = tuple(
            sorted(
                spec.entry_rules,
                key=lambda item: (item.factor, item.operator.value, item.value, item.unit),
            )
        )
        exit_rules = tuple(
            sorted(
                spec.exit_rules,
                key=lambda item: (item.factor, item.operator.value, item.value, item.unit),
            )
        )
        ranking = tuple(
            sorted(
                spec.ranking,
                key=lambda item: (item.factor, item.direction.value, item.weight),
            )
        )
        return StrategySpec(
            schema_version=spec.schema_version,
            universe=spec.universe,
            signals=signals,
            ranking=ranking,
            selection=spec.selection,
            portfolio=spec.portfolio,
            rebalance=spec.rebalance,
            backtest=spec.backtest,
            entry_rules=entry_rules,
            exit_rules=exit_rules,
            assumptions=tuple(sorted(spec.assumptions)),
        )


@dataclass(frozen=True)
class StrategyCompiler:
    """Compile a validated StrategySpec into a deterministic execution plan."""

    normalizer: StrategyNormalizer = StrategyNormalizer()

    def compile(self, spec: StrategySpec) -> ExecutionPlan:
        canonical = self.normalizer.normalize(spec)
        strategy_hash = _stable_hash(
            {
                "strategy": to_serializable(canonical),
                "versions": REGISTRY_VERSIONS,
            }
        )
        return ExecutionPlan(
            plan_id=f"plan_{strategy_hash[:12]}",
            strategy_hash=strategy_hash,
            universe=canonical.universe,
            filters=canonical.signals,
            ranking=canonical.ranking,
            selection=canonical.selection,
            portfolio=canonical.portfolio,
            rebalance=canonical.rebalance,
            backtest=canonical.backtest,
            versions=dict(REGISTRY_VERSIONS),
            entry_rules=canonical.entry_rules,
            exit_rules=canonical.exit_rules,
        )


def _universe_spec(universe_name: str | None, stock: ResolvedStock | None) -> UniverseSpec:
    if stock is not None:
        return UniverseSpec(
            name="single_stock",
            symbols=(stock.ts_code,),
            display_name=stock.name or stock.symbol,
            filters=(
                {
                    "field": "ts_code",
                    "operator": "in",
                    "value": [stock.ts_code],
                    "source": stock.source,
                },
            ),
        )
    return UniverseSpec(name=universe_name or "all_a_demo")


def compile_from_text(text: str) -> tuple[DraftStrategySpec, ValidationResult, ExecutionPlan | None]:
    parser = HeuristicStrategyParser()
    validator = StrategyValidator()
    compiler = StrategyCompiler()
    draft = parser.parse(text)
    validation = validator.validate(draft)
    plan = compiler.compile(validation.spec) if validation.is_valid else None
    return draft, validation, plan


def _find_mentioned_factors(text: str) -> list[str]:
    normalized = text.lower().replace(" ", "")
    matches: list[tuple[int, int, str]] = []
    registry_match_spans: list[tuple[int, int]] = []
    for factor_name, definition in FACTOR_REGISTRY.items():
        alias_matches: list[tuple[int, int, str]] = []
        for alias in definition.aliases + (factor_name,):
            token = alias.lower().replace(" ", "")
            index = normalized.find(token)
            if index >= 0:
                alias_matches.append((index, len(token), token))
        if alias_matches:
            index, length, token = sorted(
                alias_matches,
                key=lambda item: (item[0], -item[1], item[2]),
            )[0]
            matches.append((index, 0, factor_name))
            registry_match_spans.append((index, index + length))
    for candidate_rank, match in enumerate(search_factor_matches(text), start=1):
        factor_name = match.factor_name
        index = normalized.find(factor_name.lower().replace(" ", ""))
        if index < 0:
            aliases = factor_aliases(factor_name)
            alias_indexes = [
                normalized.find(alias.lower().replace(" ", ""))
                for alias in aliases
                if normalized.find(alias.lower().replace(" ", "")) >= 0
            ]
            term_indexes = [
                normalized.find(term.lower().replace(" ", ""))
                for term in match.matched_terms
                if normalized.find(term.lower().replace(" ", "")) >= 0
            ]
            indexes = [*alias_indexes, *term_indexes]
            index = min(indexes) if indexes else len(normalized)
        matches.append((index, candidate_rank, factor_name))
    ordered = []
    seen = set()
    registry_match_indexes = {
        index for index, priority, _factor in matches if priority == 0
    }
    for index, priority, factor in sorted(matches, key=lambda item: (item[0], item[1], item[2])):
        if priority > 0 and index in registry_match_indexes:
            continue
        if priority > 0 and any(start <= index < end for start, end in registry_match_spans):
            continue
        if factor in seen:
            continue
        seen.add(factor)
        ordered.append(factor)
    return ordered


def _parse_lookback_years(text: str) -> int | None:
    normalized = text.lower()
    match = re.search(r"(?:过去|近|最近)?\s*([0-9]+|[一二两三四五六七八九十]{1,3})\s*年", normalized)
    if not match:
        return None
    return _parse_int(match.group(1))


def _parse_selection_count(text: str) -> int | None:
    patterns = (
        r"(?:前|后|top|bottom)\s*([0-9]+|[一二两三四五六七八九十]{1,3})(?!\s*[%％])\s*(?:只|个|支)?",
        r"(?:选|选择|持有)\s*([0-9]+|[一二两三四五六七八九十]{1,3})\s*(?:只|个|支)",
        r"([0-9]+|[一二两三四五六七八九十]{1,3})\s*(?:只|支)\s*(?:股票|标的)?",
    )
    for pattern in patterns:
        match = re.search(pattern, text.lower())
        if match:
            return _parse_int(match.group(1))
    return None


def _parse_frequency(text: str) -> RebalanceFrequency | None:
    normalized = text.lower()
    if any(word in normalized for word in ("每周", "周度", "weekly")):
        return RebalanceFrequency.WEEKLY
    if any(word in normalized for word in ("每月", "月度", "monthly")):
        return RebalanceFrequency.MONTHLY
    if any(word in normalized for word in ("每季", "季度", "quarterly")):
        return RebalanceFrequency.QUARTERLY
    if any(
        word in normalized
        for word in ("触发", "达到", "满足条件", "一旦", "有信号", "信号触发", "trigger", "signal")
    ):
        return RebalanceFrequency.TRIGGERED
    return None


def _parse_benchmark(text: str) -> str:
    normalized = text.lower()
    if "中证500" in normalized or "000905" in normalized:
        return "000905.SH"
    if "中证1000" in normalized or "000852" in normalized:
        return "000852.SH"
    return "000300.SH"


def _parse_action_factor_rules(
    text: str,
    action_keywords: tuple[str, ...],
) -> list[FactorCondition]:
    rules: list[FactorCondition] = []
    seen: set[tuple[str, Operator, float, str]] = set()
    for condition in _parse_action_percentile_rules(text, action_keywords):
        key = _condition_key(condition)
        if key in seen:
            continue
        seen.add(key)
        rules.append(condition)
    for clause in _split_clauses(text):
        normalized = clause.lower()
        if not any(keyword in normalized for keyword in action_keywords):
            continue
        for condition in _parse_action_percentile_rules(clause, action_keywords):
            key = _condition_key(condition)
            if key in seen:
                continue
            seen.add(key)
            rules.append(condition)
        for factor in _find_mentioned_factors(clause):
            for condition in _parse_factor_filters(clause, factor):
                key = _condition_key(condition)
                if key in seen:
                    continue
                seen.add(key)
                rules.append(condition)
    return rules


_PE_RANK_PERCENTILE_RE = re.compile(
    r"(?:历史)?\s*(前|后)\s*(?:历史)?\s*([0-9]+(?:\.[0-9]+)?)\s*[%％]"
)
_PE_COMPARISON_PERCENTILE_RE = re.compile(
    r"(?:(?:pe|市盈率|市盈)\s*)?"
    r"(低于等于|小于等于|不高于|低于|小于|<=|<|高于等于|大于等于|不低于|高于|大于|超过|>=|>)"
    r"\s*([0-9]+(?:\.[0-9]+)?)\s*[%％]"
    r"(?:\s*(?:历史)?(?:分位|百分位))?"
    r"(?:\s*(?:pe|市盈率|市盈))?"
)
_PE_ALIASES = ("pe", "市盈率", "市盈", "市盈率ttm")


def _parse_action_percentile_rules(
    clause: str,
    action_keywords: tuple[str, ...],
) -> list[FactorCondition]:
    normalized = clause.lower()
    rules: list[FactorCondition] = []
    for keyword in action_keywords:
        token = keyword.lower()
        start = 0
        while True:
            index = normalized.find(token, start)
            if index < 0:
                break
            prefix = normalized[max(0, index - 40) : index]
            context = normalized[max(0, index - 60) : index + len(token) + 6]
            if not any(alias in context for alias in _PE_ALIASES):
                start = index + len(token)
                continue
            condition = _pe_percentile_condition(prefix)
            if condition is None:
                start = index + len(token)
                continue
            operator, value = condition
            rules.append(
                FactorCondition(
                    factor="earnings_to_price",
                    operator=operator,
                    value=value,
                    unit="percentile",
                )
            )
            start = index + len(token)
    return rules


def _pe_percentile_condition(prefix: str) -> tuple[Operator, float] | None:
    rank_matches = [
        (match.start(), _condition_from_pe_rank(match.group(1), match.group(2)))
        for match in _PE_RANK_PERCENTILE_RE.finditer(prefix)
    ]
    comparison_matches = [
        (match.start(), _condition_from_pe_comparison(match.group(1), match.group(2)))
        for match in _PE_COMPARISON_PERCENTILE_RE.finditer(prefix)
    ]
    matches = rank_matches + comparison_matches
    if not matches:
        return None
    return sorted(matches, key=lambda item: item[0])[-1][1]


def _condition_from_pe_rank(position: str, raw_percent: str) -> tuple[Operator, float]:
    percent = float(raw_percent) / 100.0
    if position == "前":
        return Operator.LESS_EQUAL, percent
    return Operator.GREATER_EQUAL, 1.0 - percent


def _condition_from_pe_comparison(operator_text: str, raw_percent: str) -> tuple[Operator, float]:
    percent = float(raw_percent) / 100.0
    if operator_text in {"低于等于", "小于等于", "不高于", "低于", "小于", "<=", "<"}:
        return Operator.GREATER_EQUAL, 1.0 - percent
    return Operator.LESS_EQUAL, 1.0 - percent


def _split_clauses(text: str) -> list[str]:
    clauses = []
    for raw_clause in _CLAUSE_SPLIT_RE.split(text):
        clause = raw_clause.strip()
        if clause:
            clauses.append(clause)
    return clauses or [text]


def _parse_ranking_rules(
    text: str,
    filters: tuple[FactorCondition, ...],
) -> list[RankingRule]:
    filter_factors = {condition.factor for condition in filters}
    rules = []
    for factor in _find_mentioned_factors(text):
        direction = _parse_direction(text, factor)
        if direction is None:
            continue
        if factor in filter_factors and _factor_mention_is_only_threshold(text, factor):
            continue
        rules.append(RankingRule(factor=factor, direction=direction))

    seen = set()
    deduped = []
    for rule in rules:
        key = (rule.factor, rule.direction)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(rule)
    return deduped


def _parse_direction(text: str, factor: str) -> SortDirection | None:
    normalized = text.lower().replace(" ", "")
    aliases = _factor_aliases_for_text(text, factor)
    high_words = ("最高", "最大", "更高", "较高", "高", "大", "多", "强", "好", "优", "top", "highest")
    low_words = ("最低", "最小", "更低", "较低", "低", "小", "少", "弱", "差", "bottom", "lowest")
    for alias in aliases:
        token = alias.lower().replace(" ", "")
        index = normalized.find(token)
        if index < 0:
            continue
        after = normalized[index + len(token) : index + len(token) + 10]
        if _looks_like_threshold_operator(after):
            continue
        window = normalized[max(0, index - 6) : index + len(token) + 8]
        if any(word in window for word in ("从小到大", "升序", "ascending")):
            return SortDirection.ASC
        if any(word in window for word in ("从大到小", "降序", "descending")):
            return SortDirection.DESC
        if any(word in window for word in low_words):
            return SortDirection.ASC
        if any(word in window for word in high_words):
            return SortDirection.DESC
    return None


def _parse_all_factor_filters(
    text: str,
    exclude_keys: set[tuple[str, Operator, float, str]] | None = None,
) -> list[FactorCondition]:
    exclude_keys = exclude_keys or set()
    filters: list[FactorCondition] = []
    seen: set[tuple[str, Operator, float, str]] = set()
    for factor in _find_mentioned_factors(text):
        for condition in _parse_factor_filters(text, factor):
            key = _condition_key(condition)
            if key in exclude_keys:
                continue
            if key in seen:
                continue
            seen.add(key)
            filters.append(condition)
    return filters


def _parse_factor_filters(text: str, factor: str) -> list[FactorCondition]:
    normalized = text.lower()
    aliases = _factor_aliases_for_text(text, factor)
    filters = []
    for alias in aliases:
        token = re.escape(alias.lower())
        for pattern, operator in (
            (rf"{token}\s*(?:大于等于|不低于|>=)\s*([0-9]+(?:\.[0-9]+)?%?)", Operator.GREATER_EQUAL),
            (rf"{token}\s*(?:大于|超过|高于|>)\s*([0-9]+(?:\.[0-9]+)?%?)", Operator.GREATER_THAN),
            (rf"{token}\s*(?:小于等于|不高于|<=)\s*([0-9]+(?:\.[0-9]+)?%?)", Operator.LESS_EQUAL),
            (rf"{token}\s*(?:小于|低于|<)\s*([0-9]+(?:\.[0-9]+)?%?)", Operator.LESS_THAN),
            (rf"(?:大于等于|不低于|>=)\s*([0-9]+(?:\.[0-9]+)?%?)\s*(?:的)?{token}", Operator.GREATER_EQUAL),
            (rf"(?:大于|超过|高于|>)\s*([0-9]+(?:\.[0-9]+)?%?)\s*(?:的)?{token}", Operator.GREATER_THAN),
            (rf"(?:小于等于|不高于|<=)\s*([0-9]+(?:\.[0-9]+)?%?)\s*(?:的)?{token}", Operator.LESS_EQUAL),
            (rf"(?:小于|低于|<)\s*([0-9]+(?:\.[0-9]+)?%?)\s*(?:的)?{token}", Operator.LESS_THAN),
        ):
            match = re.search(pattern, normalized)
            if not match:
                continue
            value = _parse_numeric_value(match.group(1), factor)
            filters.append(FactorCondition(factor=factor, operator=operator, value=value))
    return filters


def _condition_key(condition: FactorCondition) -> tuple[str, Operator, float, str]:
    return (condition.factor, condition.operator, condition.value, condition.unit)


def _factor_mention_is_only_threshold(text: str, factor: str) -> bool:
    normalized = text.lower().replace(" ", "")
    for alias in _factor_aliases_for_text(text, factor):
        token = alias.lower().replace(" ", "")
        index = normalized.find(token)
        if index < 0:
            continue
        after = normalized[index + len(token) : index + len(token) + 12]
        if _looks_like_threshold_operator(after):
            return True
    return False


def _looks_like_threshold_operator(text_after_factor: str) -> bool:
    return bool(
        re.match(
            r"(大于等于|不低于|>=|大于|超过|高于|>|小于等于|不高于|<=|小于|低于|<)\s*[0-9]",
            text_after_factor,
        )
    )


def _validate_factor_conditions(
    path: str,
    conditions: tuple[FactorCondition, ...],
) -> list[ValidationIssue]:
    issues = []
    for condition in conditions:
        if not _factor_is_supported(condition.factor):
            issues.append(
                ValidationIssue(
                    path=path,
                    code="unsupported_factor",
                    message=f"未注册因子：{condition.factor}",
                )
            )
        if condition.unit == "percentile" and not 0 <= condition.value <= 1:
            issues.append(
                ValidationIssue(
                    path=path,
                    code="invalid_percentile_threshold",
                    message=f"{condition.factor} 的分位阈值必须在 0 到 1 之间。",
                )
            )
    return issues


def _factor_is_supported(factor: str) -> bool:
    return factor in FACTOR_REGISTRY or factor_exists(factor)


def _find_contradictory_filters(filters: tuple[FactorCondition, ...]) -> list[ValidationIssue]:
    issues = []
    by_factor: dict[tuple[str, str], dict[str, float]] = {}
    for condition in filters:
        bounds = by_factor.setdefault((condition.factor, condition.unit), {})
        if condition.operator in {Operator.GREATER_THAN, Operator.GREATER_EQUAL}:
            bounds["lower"] = max(bounds.get("lower", float("-inf")), condition.value)
        elif condition.operator in {Operator.LESS_THAN, Operator.LESS_EQUAL}:
            bounds["upper"] = min(bounds.get("upper", float("inf")), condition.value)

    for (factor, _unit), bounds in by_factor.items():
        if bounds.get("lower", float("-inf")) > bounds.get("upper", float("inf")):
            issues.append(
                ValidationIssue(
                    path=f"signals.{factor}",
                    code="contradictory_filter",
                    message=f"{factor} 的上下界条件冲突。",
                )
            )
    return issues


def _parse_numeric_value(value: str, factor: str) -> float:
    raw = value.strip()
    if raw.endswith("%"):
        return float(raw[:-1]) / 100.0
    parsed = float(raw)
    if _factor_unit(factor) == "decimal" and parsed > 1:
        return parsed / 100.0
    return parsed


def _factor_aliases(factor: str) -> tuple[str, ...]:
    if factor in FACTOR_REGISTRY:
        return FACTOR_REGISTRY[factor].aliases + (factor,)
    return factor_aliases(factor) or (factor,)


def _factor_aliases_for_text(text: str, factor: str) -> tuple[str, ...]:
    aliases = list(_factor_aliases(factor))
    owner_by_term: dict[str, str] = {}
    matches = search_factor_matches(text)
    for match in matches:
        for term in match.matched_terms:
            normalized = term.lower().replace(" ", "")
            if normalized:
                owner_by_term.setdefault(normalized, match.factor_name)
    for match in matches:
        if match.factor_name != factor:
            continue
        for term in match.matched_terms:
            normalized = term.lower().replace(" ", "")
            if normalized and owner_by_term.get(normalized) == factor:
                aliases.append(term)
    return tuple(dict.fromkeys(alias for alias in aliases if alias))


def _factor_default_direction(factor: str) -> str:
    if factor in FACTOR_REGISTRY:
        return FACTOR_REGISTRY[factor].direction
    return "higher_is_better"


def _factor_unit(factor: str) -> str:
    if factor in FACTOR_REGISTRY:
        return FACTOR_REGISTRY[factor].unit
    return "raw"


def _parse_int(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if "十" in value:
        prefix, _, suffix = value.partition("十")
        tens = _CN_NUMBERS.get(prefix, 1) if prefix else 1
        ones = _CN_NUMBERS.get(suffix, 0) if suffix else 0
        return tens * 10 + ones
    return _CN_NUMBERS.get(value)


def _stable_hash(payload: dict[str, Any]) -> str:
    data = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
