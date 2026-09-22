# Evaluation Engine 固定规则评分设计

> 文档版本：v1.0  
> 输入：`BacktestFacts`、`MetricsResult`、`EvaluationProfile`  
> 输出：`StrategyAssessment`

## 1. 目标

Evaluation Engine 将已计算的事实指标转换为可信度状态、维度评分、评级、优势、弱点和警告。

核心原则：

> 先判断回测是否可信，再评价策略是否优秀。评分由版本化规则生成，不由 LLM 主观决定。

## 2. 评价结构

```text
Validity Gate
    ↓ pass
Dimension Scoring
    ↓
Penalty Rules
    ↓
Final Score
    ↓
Rating + Evidence
```

如果 Validity Gate 不通过：

```json
{
  "status": "invalid_backtest",
  "rating": null,
  "total_score": null
}
```

## 3. 八个评价角度

| 维度 | 主要回答 | 示例指标 |
|---|---|---|
| 可信度 | 回测是否可信 | 未来函数、数据覆盖率、可复现性 |
| 收益能力 | 是否赚钱及跑赢基准 | CAGR、超额收益、Alpha |
| 风险水平 | 可能亏多少 | 最大回撤、波动率、CVaR |
| 风险调整收益 | 风险是否值得 | Sharpe、Sortino、Calmar、IR |
| 稳定性 | 是否依赖少数时期 | 月/年胜率、Rolling Sharpe、样本外表现 |
| 实盘可行性 | 收益能否实现 | 换手、成本拖累、拒单率、容量 |
| 集中度 | 是否过度集中 | Top10 权重、行业集中度、有效持仓数 |
| 逻辑与归因 | 为什么赚钱 | 因子暴露、分组单调性、IC、贡献度 |

可信度是硬门槛，其余七项参与评分。

## 4. Validity Gate

### 4.1 硬失败条件

| 规则 ID | 条件 | 结果 |
|---|---|---|
| `VG_LOOKAHEAD` | 确认存在未来函数 | `fail` |
| `VG_SURVIVORSHIP` | 严重生存者偏差 | `fail` |
| `VG_NO_COST` | 高频或高换手策略未计成本 | `fail` |
| `VG_REPRODUCIBILITY` | 相同输入无法复现 | `fail` |
| `VG_NAV_INVALID` | 净值无法由收益复算 | `fail` |
| `VG_EXECUTION_BYPASS` | 无视停牌或涨跌停直接成交 | `fail` |

### 4.2 警告条件

| 规则 ID | 条件 | 结果 |
|---|---|---|
| `VW_SHORT_HISTORY` | 回测历史不足配置要求 | `warning` |
| `VW_LOW_COVERAGE` | 财务或行情覆盖率偏低 | `warning` |
| `VW_NO_OOS` | 缺少样本外测试 | `warning` |
| `VW_FEW_REGIMES` | 未覆盖多种市场环境 | `warning` |
| `VW_SMALL_TRADE_SAMPLE` | 交易样本量过少 | `warning` |

## 5. Evaluation Profile

评分阈值必须根据策略类型配置，不能硬编码为全系统唯一标准。

```yaml
profile_id: cn_long_only_equity
version: 1.0.0
benchmark_required: true
minimum_backtest_years: 5
minimum_oos_years: 1
weights:
  return: 0.15
  risk: 0.15
  risk_adjusted: 0.20
  stability: 0.20
  tradability: 0.15
  concentration: 0.05
  explainability: 0.10
```

其他 Profile 示例：

```text
cn_long_only_equity
market_neutral_equity
futures_trend
low_volatility_income
high_turnover_factor
```

## 6. 指标评分函数

### 6.1 分段线性评分

```yaml
sharpe_ratio:
  direction: higher_is_better
  points:
    - [0.0, 0]
    - [0.5, 40]
    - [1.0, 70]
    - [1.5, 90]
    - [2.0, 100]
```

两个节点之间线性插值，超出范围截断至 0—100。

### 6.2 越低越好的指标

```yaml
abs_max_drawdown:
  direction: lower_is_better
  points:
    - [0.10, 100]
    - [0.20, 80]
    - [0.30, 55]
    - [0.50, 20]
    - [0.70, 0]
```

以上仅为 Profile 示例，不代表所有策略的通用投资标准。

### 6.3 相对评分

部分指标应相对基准评价：

```text
annual_excess_return
information_ratio
monthly_outperformance_rate
down_capture_ratio
```

## 7. 维度评分

### 7.1 收益能力

```text
return_score =
    CAGR_score × 30%
    + annual_excess_return_score × 35%
    + alpha_score × 20%
    + yearly_outperformance_score × 15%
```

### 7.2 风险水平

```text
risk_score =
    max_drawdown_score × 45%
    + drawdown_duration_score × 20%
    + volatility_score × 15%
    + CVaR_score × 20%
```

### 7.3 风险调整收益

```text
risk_adjusted_score =
    sharpe_score × 30%
    + sortino_score × 20%
    + calmar_score × 30%
    + information_ratio_score × 20%
```

### 7.4 稳定性

```text
stability_score =
    rolling_sharpe_score × 25%
    + monthly_outperformance_score × 15%
    + positive_year_score × 15%
    + regime_consistency_score × 20%
    + parameter_robustness_score × 10%
    + out_of_sample_score × 15%
```

### 7.5 实盘可行性

```text
tradability_score =
    turnover_score × 20%
    + cost_drag_score × 30%
    + rejected_order_score × 20%
    + liquidity_score × 20%
    + capacity_score × 10%
```

### 7.6 集中度

```text
concentration_score =
    effective_holdings_score × 30%
    + max_position_score × 30%
    + industry_concentration_score × 40%
```

### 7.7 逻辑与归因

```text
explainability_score =
    factor_exposure_score × 25%
    + quantile_monotonicity_score × 25%
    + IC_stability_score × 25%
    + contribution_concentration_score × 25%
```

## 8. 缺失指标处理

缺失指标不得自动记为 0 分。

规则：

1. 指标非必需时，从维度中移除并重新归一化权重。
2. 指标为必需时，该维度标记 `not_scored`。
3. 缺失源于数据质量问题时，额外生成警告或惩罚。
4. 维度缺少超过配置比例的权重时，不生成总分。

## 9. 惩罚规则

惩罚应单独显示，不隐藏在指标分数中。

```yaml
penalties:
  excessive_cost_drag:
    when: transaction_cost_drag > 0.05
    points: -10
  severe_recent_decay:
    when: rolling_1y_sharpe_recent < 0
    points: -8
  industry_concentration:
    when: max_industry_weight > 0.40
    points: -5
```

```text
final_score = clamp(weighted_dimension_score + penalty_points, 0, 100)
```

## 10. 评级映射

```yaml
rating:
  A: [90, 100]
  B: [75, 90]
  C: [60, 75]
  D: [40, 60]
  E: [0, 40]
```

建议避免在产品中把评级描述成“买入/卖出”。评级表示回测质量和策略研究表现，不等于未来收益承诺。

## 11. 输出协议

```json
{
  "schema_version": "1.0.0",
  "evaluation_profile": "cn_long_only_equity",
  "evaluation_profile_version": "1.0.0",
  "status": "valid",
  "validity": {
    "status": "pass",
    "failed_rules": [],
    "warnings": []
  },
  "rating": "B",
  "total_score": 78.5,
  "dimension_scores": {
    "return": 82.0,
    "risk": 74.0,
    "risk_adjusted": 85.0,
    "stability": 72.0,
    "tradability": 68.0,
    "concentration": 80.0,
    "explainability": 77.0
  },
  "penalties": [],
  "strengths": [],
  "weaknesses": [],
  "evidence": [],
  "warnings": []
}
```

### 11.1 证据字段

```json
{
  "conclusion_id": "STRENGTH_EXCESS_RETURN",
  "message_key": "positive_long_term_excess_return",
  "metric_refs": [
    "summary.performance.annual_excess_return",
    "summary.benchmark.information_ratio"
  ],
  "rule_id": "ER_EXCESS_001"
}
```

LLM 根据 `message_key` 和 `metric_refs` 解释结论，而不是重新决定结论。

## 12. 规则版本与审计

每次评价保存：

- Profile ID 和版本。
- 所有规则 ID。
- 原始指标值。
- 插值后的指标得分。
- 维度权重。
- 惩罚项。
- 最终评分计算过程。

## 13. 验收标准

- Validity Gate 失败时不会生成评级。
- 相同指标与 Profile 得到相同评分。
- 所有分数都可以追溯到指标、规则和配置版本。
- 缺失指标不会被静默转成 0。
- 修改 Profile 不会改变 MetricsResult。
- LLM 不参与数值评分和评级生成。

