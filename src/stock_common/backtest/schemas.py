"""Data contracts for the deterministic factor-backtest pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date
from enum import Enum
from typing import Any


class StrategyStatus(str, Enum):
    VALID = "valid"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNSUPPORTED = "unsupported"
    INVALID = "invalid"


class Operator(str, Enum):
    GREATER_THAN = ">"
    GREATER_EQUAL = ">="
    LESS_THAN = "<"
    LESS_EQUAL = "<="
    EQUAL = "=="


class SortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


class SelectionMethod(str, Enum):
    TOP_N = "top_n"


class WeightingMethod(str, Enum):
    EQUAL_WEIGHT = "equal_weight"


class RebalanceFrequency(str, Enum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    TRIGGERED = "triggered"


class DataStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class UniverseSpec:
    market: str = "CN_A"
    name: str = "all_a_demo"
    symbols: tuple[str, ...] = ()
    display_name: str | None = None
    filters: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.symbols, tuple):
            object.__setattr__(
                self,
                "symbols",
                tuple(str(symbol).strip().upper() for symbol in self.symbols if str(symbol).strip()),
            )


@dataclass(frozen=True)
class FactorCondition:
    factor: str
    operator: Operator | str
    value: float
    unit: str = "decimal"

    def __post_init__(self) -> None:
        if not isinstance(self.operator, Operator):
            object.__setattr__(self, "operator", Operator(self.operator))


@dataclass(frozen=True)
class RankingRule:
    factor: str
    direction: SortDirection | str
    weight: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.direction, SortDirection):
            object.__setattr__(self, "direction", SortDirection(self.direction))


@dataclass(frozen=True)
class SelectionSpec:
    method: SelectionMethod | str = SelectionMethod.TOP_N
    count: int = 20

    def __post_init__(self) -> None:
        if not isinstance(self.method, SelectionMethod):
            object.__setattr__(self, "method", SelectionMethod(self.method))
        if self.count <= 0:
            raise ValueError("selection.count must be positive")


@dataclass(frozen=True)
class PortfolioSpec:
    weighting: WeightingMethod | str = WeightingMethod.EQUAL_WEIGHT
    max_position_weight: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.weighting, WeightingMethod):
            object.__setattr__(self, "weighting", WeightingMethod(self.weighting))
        if self.max_position_weight is not None and not 0 < self.max_position_weight <= 1:
            raise ValueError("portfolio.max_position_weight must be in (0, 1]")


@dataclass(frozen=True)
class RebalanceSpec:
    frequency: RebalanceFrequency | str | None = None
    signal_at: str = "close"
    trade_at: str = "next_open"

    def __post_init__(self) -> None:
        if self.frequency is None:
            object.__setattr__(self, "frequency", RebalanceFrequency.MONTHLY)
        elif not isinstance(self.frequency, RebalanceFrequency):
            object.__setattr__(self, "frequency", RebalanceFrequency(self.frequency))


@dataclass(frozen=True)
class BacktestSpec:
    lookback_years: int
    benchmark: str = "000300.SH"
    end_date: date | None = None
    initial_capital: float = 1_000_000.0

    def __post_init__(self) -> None:
        if self.lookback_years <= 0:
            raise ValueError("backtest.lookback_years must be positive")
        if self.initial_capital <= 0:
            raise ValueError("backtest.initial_capital must be positive")


@dataclass(frozen=True)
class StrategySpec:
    schema_version: str
    universe: UniverseSpec
    signals: tuple[FactorCondition, ...]
    ranking: tuple[RankingRule, ...]
    selection: SelectionSpec
    portfolio: PortfolioSpec
    rebalance: RebalanceSpec
    backtest: BacktestSpec
    entry_rules: tuple[FactorCondition, ...] = ()
    exit_rules: tuple[FactorCondition, ...] = ()
    assumptions: tuple[str, ...] = ()


@dataclass(frozen=True)
class DraftStrategySpec:
    schema_version: str
    status: StrategyStatus | str
    strategy: StrategySpec | None = None
    unresolved_fields: tuple[dict[str, Any], ...] = ()
    assumptions: tuple[str, ...] = ()
    field_confidence: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.status, StrategyStatus):
            object.__setattr__(self, "status", StrategyStatus(self.status))


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    code: str
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class ValidationResult:
    status: StrategyStatus | str
    spec: StrategySpec | None = None
    issues: tuple[ValidationIssue, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, StrategyStatus):
            object.__setattr__(self, "status", StrategyStatus(self.status))

    @property
    def is_valid(self) -> bool:
        return self.status is StrategyStatus.VALID and self.spec is not None


@dataclass(frozen=True)
class ExecutionPlan:
    plan_id: str
    strategy_hash: str
    universe: UniverseSpec
    filters: tuple[FactorCondition, ...]
    ranking: tuple[RankingRule, ...]
    selection: SelectionSpec
    portfolio: PortfolioSpec
    rebalance: RebalanceSpec
    backtest: BacktestSpec
    versions: dict[str, str]
    entry_rules: tuple[FactorCondition, ...] = ()
    exit_rules: tuple[FactorCondition, ...] = ()


@dataclass(frozen=True)
class PortfolioDaily:
    date: date
    cash: float
    positions_value: float
    gross_portfolio_value: float
    net_portfolio_value: float
    benchmark_value: float
    gross_return: float
    net_return: float
    benchmark_return: float
    holding_count: int
    cash_ratio: float
    gross_exposure: float
    net_exposure: float
    turnover: float = 0.0


@dataclass(frozen=True)
class PositionSnapshot:
    date: date
    symbol: str
    quantity: float
    close: float
    market_value: float
    weight: float
    cost_basis: float | None = None
    unrealized_pnl: float = 0.0
    industry_code: str | None = None
    market_cap: float | None = None


@dataclass(frozen=True)
class OrderRecord:
    order_id: str
    created_at: str
    symbol: str
    side: str
    order_type: str
    requested_quantity: float
    reference_price: float
    status: str
    reason: str | None
    signal_id: str


@dataclass(frozen=True)
class FillRecord:
    fill_id: str
    order_id: str
    timestamp: str
    symbol: str
    side: str
    quantity: float
    price: float
    amount: float
    commission: float
    tax: float
    slippage_cost: float


@dataclass(frozen=True)
class CostRecord:
    date: date
    commission: float
    tax: float
    slippage_cost: float
    market_impact_cost: float | None
    borrow_cost: float | None
    total_cost: float


@dataclass(frozen=True)
class BacktestFacts:
    schema_version: str
    status: DataStatus | str
    metadata: dict[str, Any]
    data_quality: dict[str, Any]
    portfolio_daily: tuple[PortfolioDaily, ...]
    positions: tuple[PositionSnapshot, ...] = ()
    orders: tuple[OrderRecord, ...] = ()
    fills: tuple[FillRecord, ...] = ()
    closed_trades: tuple[dict[str, Any], ...] = ()
    costs: tuple[CostRecord, ...] = ()
    exposures: tuple[dict[str, Any], ...] = ()
    events: tuple[dict[str, Any], ...] = ()
    warnings: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, DataStatus):
            object.__setattr__(self, "status", DataStatus(self.status))


@dataclass(frozen=True)
class MetricsResult:
    schema_version: str
    summary: dict[str, Any]
    series: dict[str, list[dict[str, Any]]]
    warnings: tuple[dict[str, Any], ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class StrategyAssessment:
    schema_version: str
    status: str
    rating: str | None
    score: float | None
    dimension_scores: dict[str, float | None]
    strengths: tuple[dict[str, Any], ...]
    weaknesses: tuple[dict[str, Any], ...]
    warnings: tuple[dict[str, Any], ...]
    evidence_refs: tuple[dict[str, Any], ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ChartArtifact:
    chart_id: str
    title: str
    chart_type: str
    priority: str
    data: list[dict[str, Any]]
    status: str = "ready"
    warnings: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ChartBundle:
    schema_version: str
    charts: tuple[ChartArtifact, ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class InterpretationResult:
    schema_version: str
    headline: str
    summary: str
    sections: tuple[dict[str, Any], ...]
    risk_disclosure: str
    evidence_refs: tuple[dict[str, Any], ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PipelineResult:
    status: StrategyStatus | str
    content: str
    draft: DraftStrategySpec
    validation: ValidationResult | None = None
    plan: ExecutionPlan | None = None
    facts: BacktestFacts | None = None
    metrics: MetricsResult | None = None
    assessment: StrategyAssessment | None = None
    charts: ChartBundle | None = None
    interpretation: InterpretationResult | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, StrategyStatus):
            object.__setattr__(self, "status", StrategyStatus(self.status))


def to_serializable(value: Any) -> Any:
    """Convert pipeline dataclasses into JSON-serializable objects."""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, date):
        return value.isoformat()
    if is_dataclass(value):
        return {
            item.name: to_serializable(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, dict):
        return {str(key): to_serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_serializable(item) for item in value]
    return value
