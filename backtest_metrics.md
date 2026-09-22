# 固定策略回测指标与图表规范

> 文档状态：v1.0  
> 适用范围：日频股票策略、固定规则选股策略、因子策略  
> 目标：统一回测引擎、结果协议与前端 Dashboard 的指标口径

## 1. 设计目标

回测结果需要依次回答六个问题：

1. 策略赚了多少？
2. 为收益承担了多大风险？
3. 收益是否稳定、是否正在衰减？
4. 交易成本是否会吞噬收益？
5. 收益来自哪些持仓、行业或因子？
6. 策略是否具备实盘可执行性？

本文档同时定义：

- 字段名称与数据类型
- 指标公式与统一计算口径
- 每个指标所需的输入数据
- 前端图表与后端字段的对应关系
- P0、P1、P2 的实现优先级
- 数据质量和防未来函数检查

---

## 2. 全局计算约定

### 2.1 基础约定

| 项目 | 默认值 | 说明 |
|---|---:|---|
| 收益率单位 | 小数 | `0.12` 表示 `12%`，禁止后端存储为 `12` |
| 净值起点 | `1.0` | 策略、基准、毛净值统一从 `1.0` 开始 |
| 年化交易日 | `252` | 参数名为 `annualization_factor` |
| 无风险利率 | `0.0` | 参数名为 `risk_free_rate_annual`，允许配置 |
| 价格口径 | 前复权或总回报口径 | 必须在元数据中明确，策略和基准保持一致 |
| 时间标准 | `YYYY-MM-DD` | 日频结果使用交易日，不混用自然日 |
| 最大回撤符号 | 负数 | 例如 `-0.183` 表示最大回撤 `-18.3%` |
| 缺失值 | `null` | 不使用 `0` 代替无法计算的指标 |
| 换手率 | 单边换手 | 默认使用 `0.5 × 权重变化绝对值之和` |

### 2.2 必须写入的回测元数据

| 字段 | 类型 | 必填 | 示例 |
|---|---|---:|---|
| `strategy_id` | string | 是 | `roe_small_cap_v1` |
| `strategy_name` | string | 是 | `高ROE小市值策略` |
| `run_id` | string | 是 | `bt_20260914_001` |
| `start_date` | date | 是 | `2015-01-01` |
| `end_date` | date | 是 | `2025-12-31` |
| `frequency` | enum | 是 | `daily` |
| `benchmark_code` | string | 是 | `000300.SH` |
| `initial_capital` | number | 是 | `1000000.0` |
| `annualization_factor` | integer | 是 | `252` |
| `risk_free_rate_annual` | number | 是 | `0.0` |
| `price_adjustment` | enum | 是 | `forward_adjusted` |
| `rebalance_frequency` | string | 是 | `monthly` |
| `commission_rate` | number | 是 | `0.0003` |
| `sell_tax_rate` | number | 是 | `0.0005` |
| `slippage_model` | string | 是 | `fixed_bps_10` |
| `data_version` | string | 是 | `market_data_20260913` |
| `strategy_version` | string | 是 | `1.0.0` |
| `created_at` | datetime | 是 | `2026-09-14T07:30:00Z` |

### 2.3 最低输入数据要求

| 输入表 | 最低字段 | 主要用途 |
|---|---|---|
| `market_prices` | `date`, `symbol`, `open`, `high`, `low`, `close`, `volume`, `adj_factor` | 成交、收益与停牌判断 |
| `benchmark_prices` | `date`, `benchmark_code`, `close` | 基准收益与超额收益 |
| `positions` | `date`, `symbol`, `quantity`, `market_value`, `weight` | 持仓和暴露分析 |
| `portfolio_daily` | `date`, `cash`, `gross_value`, `net_value` | 净值和现金仓位 |
| `orders` | `order_id`, `date`, `symbol`, `side`, `quantity`, `status` | 成交能力与拒单分析 |
| `trades` | `trade_id`, `date`, `symbol`, `side`, `quantity`, `price`, `amount` | 换手、成本和交易分析 |
| `costs` | `date`, `commission`, `tax`, `slippage` | 毛净收益差异 |
| `security_master` | `symbol`, `list_date`, `delist_date`, `industry` | 股票池和行业分析 |
| `factor_values` | `date`, `symbol`, `factor_name`, `factor_value` | 因子暴露、分组和 IC |
| `fundamentals` | `symbol`, `report_period`, `announce_date`, `field`, `value` | 财务因子计算 |

---

## 3. 核心计算定义

### 3.1 日收益与净值

```text
strategy_return[t] = net_value[t] / net_value[t-1] - 1

benchmark_return[t] = benchmark_close[t] / benchmark_close[t-1] - 1

active_return[t] = strategy_return[t] - benchmark_return[t]

strategy_nav[t] = cumulative_product(1 + strategy_return[t])

benchmark_nav[t] = cumulative_product(1 + benchmark_return[t])

excess_nav[t] = strategy_nav[t] / benchmark_nav[t]
```

`excess_nav` 使用净值比值，不能直接对每日简单超额收益做不加说明的累计求和。

### 3.2 累计收益与年化收益

```text
total_return = ending_nav / starting_nav - 1

years = calendar_days_between_start_and_end / 365.25

annual_return = (ending_nav / starting_nav) ^ (1 / years) - 1

benchmark_annual_return =
    (ending_benchmark_nav / starting_benchmark_nav) ^ (1 / years) - 1

annual_excess_return = annual_return - benchmark_annual_return
```

说明：

- `annual_return` 表示 CAGR。
- `annual_excess_return` 用于摘要展示。
- 更严格的主动收益评价使用 `information_ratio`，不能用 CAGR 差值替代。

### 3.3 波动率与风险调整收益

```text
annual_volatility = std(strategy_return, ddof=1) × sqrt(252)

daily_risk_free_rate = (1 + risk_free_rate_annual) ^ (1 / 252) - 1

sharpe_ratio =
    mean(strategy_return - daily_risk_free_rate)
    / std(strategy_return - daily_risk_free_rate, ddof=1)
    × sqrt(252)

downside_deviation =
    sqrt(mean(min(strategy_return - daily_risk_free_rate, 0) ^ 2))
    × sqrt(252)

sortino_ratio =
    mean(strategy_return - daily_risk_free_rate) × 252
    / downside_deviation
```

若分母为 `0` 或有效样本不足，输出 `null`，不输出无穷大。

### 3.4 回撤与 Calmar

```text
running_peak[t] = max(strategy_nav[0:t])

drawdown[t] = strategy_nav[t] / running_peak[t] - 1

max_drawdown = min(drawdown)

calmar_ratio = annual_return / abs(max_drawdown)
```

最大回撤区间定义：

- `max_drawdown_start_date`：最大回撤谷底之前对应的历史净值峰值日。
- `max_drawdown_bottom_date`：`max_drawdown` 对应的交易日。
- `max_drawdown_recovery_date`：谷底后净值首次恢复至前高的交易日；未恢复时为 `null`。
- `max_drawdown_duration_days`：峰值日至恢复日的交易日数；未恢复时计算到回测结束日。

### 3.5 Alpha、Beta 与主动风险

```text
beta = covariance(strategy_return, benchmark_return)
       / variance(benchmark_return)

alpha_daily =
    mean(strategy_return - daily_risk_free_rate)
    - beta × mean(benchmark_return - daily_risk_free_rate)

alpha_annual = alpha_daily × 252

tracking_error = std(active_return, ddof=1) × sqrt(252)

information_ratio = mean(active_return) × 252 / tracking_error
```

### 3.6 周期收益与滚动指标

月度、年度收益使用周期末净值计算：

```text
period_return = period_end_nav / previous_period_end_nav - 1
```

默认滚动窗口：

| 名称 | 交易日窗口 |
|---|---:|
| `rolling_3m_*` | 63 |
| `rolling_6m_*` | 126 |
| `rolling_1y_*` | 252 |
| `rolling_3y_*` | 756 |

```text
rolling_1y_return[t] = strategy_nav[t] / strategy_nav[t-252] - 1

rolling_1y_excess_return[t] =
    (strategy_nav[t] / strategy_nav[t-252])
    / (benchmark_nav[t] / benchmark_nav[t-252]) - 1
```

滚动 Sharpe、波动率、Sortino 均仅使用窗口内收益计算；窗口数据不足时输出 `null`。

### 3.7 换手率

调仓日的单边换手率：

```text
turnover[t] = 0.5 × sum(abs(weight_after[t, i] - weight_before[t, i]))
```

要求：

- 权重向量应包含现金，或明确现金是否参与计算。
- `weight_before` 必须使用调仓前、经过市场涨跌漂移后的权重。
- 年换手率为期间单次换手率之和，不再额外乘以调仓次数。

### 3.8 交易成本

```text
commission_cost = sum(trade_amount × commission_rate)

tax_cost = sum(sell_trade_amount × sell_tax_rate)

slippage_cost = sum(abs(executed_price - reference_price) × quantity)

total_transaction_cost = commission_cost + tax_cost + slippage_cost

cost_ratio = total_transaction_cost / initial_capital

transaction_cost_drag = gross_annual_return - net_annual_return
```

所有成本同时保留“货币金额”和“占组合资产比例”两种字段。

---

## 4. 汇总指标协议

### 4.1 `performance`

| 字段 | 类型 | 公式或来源 | 必填 |
|---|---|---|---:|
| `total_return` | number | 策略累计收益 | 是 |
| `annual_return` | number | 策略 CAGR | 是 |
| `benchmark_total_return` | number | 基准累计收益 | 是 |
| `benchmark_annual_return` | number | 基准 CAGR | 是 |
| `annual_excess_return` | number | 两者 CAGR 之差 | 是 |
| `gross_total_return` | number | 未扣成本累计收益 | 是 |
| `gross_annual_return` | number | 未扣成本 CAGR | 是 |
| `net_total_return` | number | 扣除成本累计收益 | 是 |
| `net_annual_return` | number | 扣除成本 CAGR | 是 |

### 4.2 `risk`

| 字段 | 类型 | 公式或来源 | 必填 |
|---|---|---|---:|
| `annual_volatility` | number | 日收益标准差年化 | 是 |
| `max_drawdown` | number | 回撤序列最小值 | 是 |
| `max_drawdown_start_date` | date | 最大回撤起点 | 是 |
| `max_drawdown_bottom_date` | date | 最大回撤谷底 | 是 |
| `max_drawdown_recovery_date` | date/null | 恢复前高日期 | 是 |
| `max_drawdown_duration_days` | integer | 回撤持续交易日 | 是 |
| `sharpe_ratio` | number/null | 年化 Sharpe | 是 |
| `sortino_ratio` | number/null | 年化 Sortino | 是 |
| `calmar_ratio` | number/null | CAGR / 最大回撤绝对值 | 是 |

### 4.3 `benchmark`

| 字段 | 类型 | 公式或来源 | 必填 |
|---|---|---|---:|
| `alpha_annual` | number/null | CAPM 日 Alpha 线性年化 | 是 |
| `beta` | number/null | 策略与基准协方差 / 基准方差 | 是 |
| `tracking_error` | number/null | 主动收益标准差年化 | 是 |
| `information_ratio` | number/null | 主动收益年化 / 跟踪误差 | 是 |
| `daily_outperformance_rate` | number | 日度跑赢比例 | 否 |
| `monthly_outperformance_rate` | number | 月度跑赢比例 | 是 |
| `yearly_outperformance_rate` | number | 年度跑赢比例 | 是 |

### 4.4 `trading`

| 字段 | 类型 | 公式或来源 | 必填 |
|---|---|---|---:|
| `annual_turnover` | number | 年内单边换手率之和 | 是 |
| `average_monthly_turnover` | number | 月度换手均值 | 是 |
| `max_monthly_turnover` | number | 月度换手最大值 | 是 |
| `total_orders` | integer | 订单总数 | 是 |
| `total_transactions` | integer | 成交总数 | 是 |
| `rejected_order_count` | integer | 拒单或无法成交订单数 | 是 |
| `commission_cost` | number | 佣金金额 | 是 |
| `slippage_cost` | number | 滑点金额 | 是 |
| `tax_cost` | number | 税费金额 | 是 |
| `total_transaction_cost` | number | 成本金额合计 | 是 |
| `transaction_cost_drag` | number | 毛、净年化收益差 | 是 |

### 4.5 `stability`

| 字段 | 类型 | 公式或来源 | 必填 |
|---|---|---|---:|
| `positive_month_count` | integer | 月收益大于 0 的月份数 | 是 |
| `negative_month_count` | integer | 月收益小于 0 的月份数 | 是 |
| `monthly_win_rate` | number | 盈利月份 / 有效月份 | 是 |
| `positive_year_count` | integer | 年收益大于 0 的年份数 | 是 |
| `negative_year_count` | integer | 年收益小于 0 的年份数 | 是 |
| `yearly_win_rate` | number | 盈利年份 / 有效年份 | 是 |
| `best_month_return` | number | 最佳月收益 | 是 |
| `worst_month_return` | number | 最差月收益 | 是 |
| `best_year_return` | number | 最佳年收益 | 是 |
| `worst_year_return` | number | 最差年收益 | 是 |

---

## 5. 图表数据协议

所有时间序列按 `date` 升序输出。相同日期只能有一条同一维度记录。

### 5.1 P0：策略净值曲线

**图表 ID：** `nav_curve`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `date` | date | 是 | 交易日期 |
| `strategy_nav` | number | 是 | 扣成本策略净值 |
| `benchmark_nav` | number | 是 | 基准净值 |
| `excess_nav` | number | 是 | 策略净值 / 基准净值 |
| `gross_nav` | number | 是 | 未扣成本策略净值 |
| `net_nav` | number | 是 | 扣成本策略净值 |

**推荐图形：** 多折线图。默认展示 `strategy_nav`、`benchmark_nav`、`excess_nav`；毛净值对比允许切换。

### 5.2 P0：回撤曲线

**图表 ID：** `drawdown_curve`

| 字段 | 类型 | 必填 |
|---|---|---:|
| `date` | date | 是 |
| `strategy_drawdown` | number | 是 |
| `benchmark_drawdown` | number | 是 |

**推荐图形：** 负值面积图；标注最大回撤起点、谷底和恢复日。

### 5.3 P0：月度收益热力图

**图表 ID：** `monthly_return_heatmap`

| 字段 | 类型 | 必填 |
|---|---|---:|
| `year` | integer | 是 |
| `month` | integer | 是 |
| `monthly_return` | number | 是 |
| `benchmark_monthly_return` | number | 是 |
| `monthly_excess_return` | number | 是 |

**推荐图形：** 年份为行、月份为列的发散色热力图；支持策略、基准、超额三种视图。

### 5.4 P0：年度收益对比

**图表 ID：** `annual_return_comparison`

| 字段 | 类型 | 必填 |
|---|---|---:|
| `year` | integer | 是 |
| `strategy_annual_return` | number | 是 |
| `benchmark_annual_return` | number | 是 |
| `annual_excess_return` | number | 是 |

**推荐图形：** 策略和基准分组柱状图，超额收益可用标签或折线展示。

### 5.5 P0：滚动收益率

**图表 ID：** `rolling_return_curve`

| 字段 | 类型 | 必填 |
|---|---|---:|
| `date` | date | 是 |
| `window_days` | integer | 是 |
| `rolling_strategy_return` | number/null | 是 |
| `rolling_benchmark_return` | number/null | 是 |
| `rolling_excess_return` | number/null | 是 |

**推荐图形：** 折线图；默认窗口为 252 个交易日。

### 5.6 P0：滚动 Sharpe

**图表 ID：** `rolling_sharpe_curve`

| 字段 | 类型 | 必填 |
|---|---|---:|
| `date` | date | 是 |
| `window_days` | integer | 是 |
| `rolling_sharpe` | number/null | 是 |
| `rolling_sortino` | number/null | 否 |
| `rolling_volatility` | number/null | 否 |

**推荐图形：** 折线图，增加 `0` 和 `1` 两条参考线。

### 5.7 P1：换手率

**图表 ID：** `turnover_curve`

| 字段 | 类型 | 必填 |
|---|---|---:|
| `date` | date | 是 |
| `daily_turnover` | number | 是 |
| `monthly_turnover` | number/null | 是 |

**推荐图形：** 月度柱状图；调仓日可显示单日换手提示。

### 5.8 P1：交易成本

**图表 ID：** `transaction_cost_chart`

| 字段 | 类型 | 必填 |
|---|---|---:|
| `date` | date | 是 |
| `commission_cost` | number | 是 |
| `slippage_cost` | number | 是 |
| `tax_cost` | number | 是 |
| `total_transaction_cost` | number | 是 |
| `cumulative_transaction_cost` | number | 是 |

**推荐图形：** 分项堆叠柱状图 + 累计成本折线。

### 5.9 P1：毛净值对比

**图表 ID：** `gross_net_nav_curve`

| 字段 | 类型 | 必填 |
|---|---|---:|
| `date` | date | 是 |
| `gross_nav` | number | 是 |
| `net_nav` | number | 是 |
| `cost_drag_nav` | number | 是 |

```text
cost_drag_nav = gross_nav - net_nav
```

**推荐图形：** 双折线图，可对两条曲线之间区域着色。

### 5.10 P1：持仓数量与现金仓位

**图表 ID：** `portfolio_capacity_curve`

| 字段 | 类型 | 必填 |
|---|---|---:|
| `date` | date | 是 |
| `target_holding_count` | integer | 是 |
| `actual_holding_count` | integer | 是 |
| `cash_ratio` | number | 是 |

**推荐图形：** 持仓数量阶梯线 + 现金比例次坐标面积图。

### 5.11 P1：行业暴露

**图表 ID：** `industry_exposure`

| 字段 | 类型 | 必填 |
|---|---|---:|
| `date` | date | 是 |
| `industry_code` | string | 是 |
| `industry_name` | string | 是 |
| `portfolio_weight` | number | 是 |
| `benchmark_weight` | number | 是 |
| `active_industry_weight` | number | 是 |

```text
active_industry_weight = portfolio_weight - benchmark_weight
```

**推荐图形：** 最新一期横向柱状图 + 行业 × 时间热力图。

### 5.12 P1：市值暴露

**图表 ID：** `market_cap_exposure`

| 字段 | 类型 | 必填 |
|---|---|---:|
| `date` | date | 是 |
| `portfolio_mean_market_cap` | number | 是 |
| `portfolio_median_market_cap` | number | 是 |
| `benchmark_mean_market_cap` | number | 是 |
| `benchmark_median_market_cap` | number | 是 |
| `large_cap_weight` | number | 否 |
| `mid_cap_weight` | number | 否 |
| `small_cap_weight` | number | 否 |
| `micro_cap_weight` | number | 否 |

市值分组边界必须配置化，并在回测元数据中保存，不能在前端临时判断。

### 5.13 P1：因子暴露

**图表 ID：** `factor_exposure`

| 字段 | 类型 | 必填 |
|---|---|---:|
| `date` | date | 是 |
| `factor_name` | string | 是 |
| `portfolio_factor_mean` | number/null | 是 |
| `portfolio_factor_median` | number/null | 是 |
| `benchmark_factor_mean` | number/null | 是 |
| `benchmark_factor_median` | number/null | 是 |
| `standardized_exposure` | number/null | 否 |

推荐先对极端值去尾，再按股票池横截面标准化；原始暴露和标准化暴露不可混用。

### 5.14 P2：因子分组收益

**图表 ID：** `factor_quantile_return`

| 字段 | 类型 | 必填 |
|---|---|---:|
| `date` | date | 否 |
| `factor_name` | string | 是 |
| `factor_quantile` | integer | 是 |
| `quantile_return` | number | 是 |
| `quantile_excess_return` | number | 否 |
| `long_short_return` | number | 否 |

```text
long_short_return = highest_quantile_return - lowest_quantile_return
```

若因子方向为“越小越好”，多空方向必须反转，并在因子元数据中记录 `factor_direction`。

### 5.15 P2：IC 与累计 IC

**图表 ID：** `factor_ic_curve`

| 字段 | 类型 | 必填 |
|---|---|---:|
| `date` | date | 是 |
| `factor_name` | string | 是 |
| `forward_period_days` | integer | 是 |
| `ic` | number/null | 是 |
| `rank_ic` | number/null | 是 |
| `cumulative_ic` | number/null | 是 |
| `cumulative_rank_ic` | number/null | 是 |

```text
IC[t] = PearsonCorr(factor_value[t], forward_return[t:t+n])

Rank_IC[t] = SpearmanCorr(factor_value[t], forward_return[t:t+n])

ICIR = mean(IC) / std(IC, ddof=1)
```

累计 IC 用于可视化，不应被解释为可直接交易的累计收益。

### 5.16 P2：止损规则分析

**图表 ID：** `stop_loss_analysis`

| 字段 | 类型 | 必填 |
|---|---|---:|
| `date` | date | 是 |
| `stop_loss_count` | integer | 是 |
| `nav_with_stop_loss` | number | 是 |
| `nav_without_stop_loss` | number | 是 |
| `return_after_1d` | number/null | 否 |
| `return_after_5d` | number/null | 是 |
| `return_after_10d` | number/null | 否 |
| `return_after_20d` | number/null | 是 |

汇总字段：

- `total_stop_loss_count`
- `stop_loss_trigger_rate`
- `average_loss_per_stop`
- `continue_falling_ratio_20d`
- `rebound_ratio_20d`
- `loss_reduced_by_stop`
- `gain_missed_by_stop`

有止损和无止损的对照回测必须保持其他参数一致。

### 5.17 P2：单笔交易收益与持仓周期

**图表 ID：** `trade_distribution`

| 字段 | 类型 | 必填 |
|---|---|---:|
| `trade_id` | string | 是 |
| `symbol` | string | 是 |
| `entry_date` | date | 是 |
| `exit_date` | date | 是 |
| `holding_days` | integer | 是 |
| `gross_trade_return` | number | 是 |
| `net_trade_return` | number | 是 |
| `exit_reason` | enum | 是 |

推荐 `exit_reason`：`rebalance`、`stop_loss`、`take_profit`、`delisted`、`end_of_backtest`。

汇总字段：

- `trade_win_rate`
- `average_trade_return`
- `average_winning_trade`
- `average_losing_trade`
- `profit_loss_ratio`
- `best_trade`
- `worst_trade`
- `average_holding_days`
- `median_holding_days`

### 5.18 P2：收益贡献

**图表 ID：** `return_contribution`

| 字段 | 类型 | 必填 |
|---|---|---:|
| `date` | date | 否 |
| `dimension_type` | enum | 是 |
| `dimension_value` | string | 是 |
| `average_weight` | number | 否 |
| `return_contribution` | number | 是 |

`dimension_type` 推荐值：`symbol`、`industry`、`factor`。

---

## 6. Dashboard 页面映射

| 页面 | 图表 | 优先级 | 后端数据键 |
|---|---|---:|---|
| Overview | 策略/基准/超额净值 | P0 | `charts.nav` |
| Overview | 回撤曲线 | P0 | `charts.drawdown` |
| Stability | 月度收益热力图 | P0 | `charts.monthly_returns` |
| Stability | 年度收益对比 | P0 | `charts.yearly_returns` |
| Stability | 滚动收益率 | P0 | `charts.rolling_returns` |
| Stability | 滚动 Sharpe | P0 | `charts.rolling_metrics` |
| Trading | 换手率 | P1 | `charts.turnover` |
| Trading | 交易成本 | P1 | `charts.transaction_costs` |
| Trading | Gross NAV vs Net NAV | P1 | `charts.gross_net_nav` |
| Portfolio | 持仓数量与现金 | P1 | `charts.portfolio_capacity` |
| Portfolio | 行业暴露 | P1 | `charts.industry_exposure` |
| Portfolio | 市值暴露 | P1 | `charts.market_cap_exposure` |
| Portfolio | 因子暴露 | P1 | `charts.factor_exposure` |
| Factor Analysis | 因子分组收益 | P2 | `charts.factor_quantiles` |
| Factor Analysis | IC / Rank IC | P2 | `charts.factor_ic` |
| Rule Analysis | 止损分析 | P2 | `charts.stop_loss` |
| Trade Analysis | 交易收益与持仓期 | P2 | `charts.trade_distribution` |
| Attribution | 收益贡献 | P2 | `charts.return_contribution` |

### 6.1 首页指标卡

第一屏只展示：

| 展示项 | 字段 |
|---|---|
| 年化收益 | `summary.performance.annual_return` |
| 年化超额收益 | `summary.performance.annual_excess_return` |
| Sharpe | `summary.risk.sharpe_ratio` |
| 最大回撤 | `summary.risk.max_drawdown` |
| Calmar | `summary.risk.calmar_ratio` |
| 年换手率 | `summary.trading.annual_turnover` |

---

## 7. 推荐输出结构

```json
{
  "schema_version": "1.0.0",
  "metadata": {
    "strategy_id": "roe_small_cap_v1",
    "run_id": "bt_20260914_001",
    "start_date": "2015-01-01",
    "end_date": "2025-12-31",
    "frequency": "daily",
    "benchmark_code": "000300.SH",
    "annualization_factor": 252,
    "risk_free_rate_annual": 0.0,
    "price_adjustment": "forward_adjusted",
    "strategy_version": "1.0.0",
    "data_version": "market_data_20260913"
  },
  "summary": {
    "performance": {},
    "risk": {},
    "benchmark": {},
    "trading": {},
    "stability": {}
  },
  "charts": {
    "nav": [],
    "drawdown": [],
    "monthly_returns": [],
    "yearly_returns": [],
    "rolling_returns": [],
    "rolling_metrics": [],
    "turnover": [],
    "transaction_costs": [],
    "gross_net_nav": [],
    "portfolio_capacity": [],
    "industry_exposure": [],
    "market_cap_exposure": [],
    "factor_exposure": [],
    "factor_quantiles": [],
    "factor_ic": [],
    "stop_loss": [],
    "trade_distribution": [],
    "return_contribution": []
  },
  "warnings": []
}
```

### 7.1 通用响应规则

- 无法计算的标量返回 `null`。
- 无数据的序列返回空数组 `[]`。
- 指标异常、样本不足或数据缺失写入 `warnings`。
- 返回值禁止包含 `NaN`、`Infinity` 或 `-Infinity`。
- 金额字段建议使用 `number`；生产环境若需严格金额精度，可传字符串或最小货币单位整数。
- 大型时间序列可独立分页或存储，但字段语义必须与本协议一致。

---

## 8. 数据质量与回测可信度检查

### 8.1 防未来函数

- 财务数据只能从 `announce_date` 之后使用，不能按 `report_period` 直接回填。
- 当日收盘价生成的信号，最早在下一可交易时点成交。
- 指数成分股、行业分类和股票池必须使用历史时点数据。
- 因子去极值、标准化和分位数边界只能使用当期横截面数据。

### 8.2 生存者偏差

- 股票池必须包含历史退市股票。
- 上市日期、退市日期、ST 状态和暂停上市状态使用历史版本。
- 不允许直接用当前成分股列表回测历史区间。

### 8.3 成交约束

- 处理停牌、涨停买不进、跌停卖不出、成交量不足等情况。
- 明确使用开盘价、收盘价、VWAP 或下一交易日价格成交。
- 滑点应随成交金额、流动性或固定 bps 模型计算。
- 未成交订单不得被静默视为成交。

### 8.4 数值一致性校验

建议自动执行：

```text
abs(strategy_nav[-1] - cumulative_product(1 + strategy_return)[-1]) < tolerance

max(drawdown) <= 0

min(drawdown) == max_drawdown

abs(total_transaction_cost
    - commission_cost
    - slippage_cost
    - tax_cost) < tolerance

abs(sum(position_weight) + cash_ratio - 1) < tolerance
```

### 8.5 最低样本量

| 指标 | 建议最低有效样本 |
|---|---:|
| 年化波动率 / Sharpe | 60 个日收益 |
| Rolling 1Y 指标 | 252 个日收益 |
| Beta / Alpha | 60 对策略与基准日收益 |
| 月度胜率 | 12 个完整月份 |
| ICIR | 12 个有效 IC 截面 |

样本不足时允许计算但必须写入警告；严重不足时指标返回 `null`。

---

## 9. 实现优先级

### P0：第一版必须完成

- 统一元数据和日频净值数据
- 策略、基准、超额净值曲线
- 累计收益、CAGR、波动率、Sharpe、最大回撤、Calmar
- 回撤曲线
- 月度收益热力图
- 年度收益对比
- Rolling 1Y Return
- Rolling 1Y Sharpe

### P1：用于判断实盘可行性

- 单边换手率
- 佣金、税费、滑点拆分
- Gross NAV vs Net NAV
- 持仓数量与现金仓位
- 行业、市值和因子暴露
- Alpha、Beta、Tracking Error、Information Ratio

### P2：因子与规则研究

- 因子分组收益
- IC、Rank IC、ICIR、累计 IC
- 止损对照实验
- 止损后收益分析
- 单笔交易收益与持仓期分布
- 股票、行业和因子收益贡献

---

## 10. 验收标准

第一版接口达到以下条件即可验收：

- 相同输入、相同版本和相同随机种子得到相同结果。
- 策略和基准使用完全对齐的交易日期。
- 所有收益率字段均使用小数，前端统一负责百分比格式化。
- Summary 中的指标能够由 Charts 中的底层序列复算得到。
- 毛收益、净收益和各类成本可以相互核对。
- 最大回撤的起点、谷底和恢复日可以从净值序列重建。
- 回测中不存在已知未来函数和当前成分股回填历史的问题。
- 返回 JSON 能通过 Schema 校验，且不包含非标准数值。
- 每个图表在数据为空时都有明确的空状态，而不是渲染错误。

---

## 11. 后续扩展

在完成 P0—P2 后，可继续增加：

- 多基准对比
- 风格因子风险归因
- Brinson 行业配置与个股选择归因
- 容量与市场冲击成本估计
- 参数敏感性分析
- 牛市、熊市、震荡市分区间表现
- Walk-forward 与样本外测试
- Bootstrap 置信区间
- 策略组合与相关性分析

