# Backtest Engine 事实数据输出协议

> 文档版本：v1.0  
> 输出对象：`BacktestFacts`

## 1. 目标

回测引擎只负责模拟策略执行并记录发生了什么，不判断策略是否优秀。

`BacktestFacts` 必须足以支持：

- 复算全部收益和风险指标。
- 审计订单、成交、持仓与交易成本。
- 检查未来函数、生存者偏差和成交约束。
- 生成策略净值、回撤、换手和暴露图表。
- 追踪数据、策略和引擎版本。

## 2. 顶层结构

```json
{
  "schema_version": "1.0.0",
  "status": "succeeded",
  "metadata": {},
  "data_quality": {},
  "portfolio_daily": [],
  "positions": [],
  "orders": [],
  "fills": [],
  "closed_trades": [],
  "costs": [],
  "exposures": [],
  "events": [],
  "warnings": []
}
```

## 3. `metadata`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `run_id` | string | 是 | 单次回测 ID |
| `strategy_id` | string | 是 | 策略 ID |
| `strategy_hash` | string | 是 | 规范化策略 Hash |
| `start_date` | date | 是 | 回测开始日 |
| `end_date` | date | 是 | 回测结束日 |
| `frequency` | enum | 是 | `daily` 等 |
| `benchmark_code` | string | 是 | 比较基准 |
| `initial_capital` | number | 是 | 初始资金 |
| `currency` | string | 是 | `CNY` 等 |
| `data_version` | string | 是 | 数据版本 |
| `engine_version` | string | 是 | 引擎版本 |
| `execution_policy_version` | string | 是 | 成交规则版本 |
| `factor_registry_version` | string | 是 | 因子注册表版本 |
| `random_seed` | integer/null | 是 | 随机种子 |
| `created_at` | datetime | 是 | 生成时间 |

## 4. `data_quality`

| 字段 | 类型 | 说明 |
|---|---|---|
| `expected_trade_days` | integer | 预期交易日数 |
| `actual_trade_days` | integer | 实际有效交易日数 |
| `market_data_coverage` | number | 行情覆盖率 |
| `fundamental_data_coverage` | number | 财务数据覆盖率 |
| `benchmark_data_coverage` | number | 基准覆盖率 |
| `missing_price_count` | integer | 缺失价格数量 |
| `stale_price_count` | integer | 可疑连续不变价格数量 |
| `invalid_factor_count` | integer | 无效因子值数量 |
| `lookahead_checks_passed` | boolean | 时序检查是否通过 |
| `survivorship_checks_passed` | boolean | 生存者偏差检查是否通过 |

## 5. `portfolio_daily`

每个交易日一条记录：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `date` | date | 是 | 交易日期 |
| `cash` | number | 是 | 现金余额 |
| `positions_value` | number | 是 | 持仓市值 |
| `gross_portfolio_value` | number | 是 | 扣成本前组合价值 |
| `net_portfolio_value` | number | 是 | 扣成本后组合价值 |
| `benchmark_value` | number | 是 | 同起点基准价值 |
| `gross_return` | number | 是 | 当日毛收益率 |
| `net_return` | number | 是 | 当日净收益率 |
| `benchmark_return` | number | 是 | 当日基准收益率 |
| `holding_count` | integer | 是 | 实际持仓数量 |
| `cash_ratio` | number | 是 | 现金比例 |
| `gross_exposure` | number | 是 | 总多空暴露 |
| `net_exposure` | number | 是 | 净暴露 |

## 6. `positions`

| 字段 | 类型 | 必填 |
|---|---|---:|
| `date` | date | 是 |
| `symbol` | string | 是 |
| `quantity` | number | 是 |
| `close` | number | 是 |
| `market_value` | number | 是 |
| `weight` | number | 是 |
| `cost_basis` | number/null | 是 |
| `unrealized_pnl` | number | 是 |
| `industry_code` | string/null | 是 |
| `market_cap` | number/null | 是 |

## 7. `orders`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `order_id` | string | 是 | 唯一订单 ID |
| `created_at` | datetime | 是 | 订单生成时刻 |
| `symbol` | string | 是 | 证券代码 |
| `side` | enum | 是 | `buy` / `sell` |
| `order_type` | enum | 是 | 市价、限价等 |
| `requested_quantity` | number | 是 | 委托数量 |
| `reference_price` | number | 是 | 滑点参考价 |
| `status` | enum | 是 | filled/partial/rejected/deferred |
| `reason` | string/null | 是 | 无法成交原因 |
| `signal_id` | string | 是 | 对应信号 ID |

## 8. `fills`

| 字段 | 类型 | 必填 |
|---|---|---:|
| `fill_id` | string | 是 |
| `order_id` | string | 是 |
| `timestamp` | datetime | 是 |
| `symbol` | string | 是 |
| `side` | enum | 是 |
| `quantity` | number | 是 |
| `price` | number | 是 |
| `amount` | number | 是 |
| `commission` | number | 是 |
| `tax` | number | 是 |
| `slippage_cost` | number | 是 |

## 9. `closed_trades`

| 字段 | 类型 | 必填 |
|---|---|---:|
| `trade_id` | string | 是 |
| `symbol` | string | 是 |
| `entry_date` | date | 是 |
| `exit_date` | date | 是 |
| `entry_price` | number | 是 |
| `exit_price` | number | 是 |
| `holding_days` | integer | 是 |
| `gross_pnl` | number | 是 |
| `net_pnl` | number | 是 |
| `gross_return` | number | 是 |
| `net_return` | number | 是 |
| `exit_reason` | enum | 是 |

注意：`closed_trades` 的交易胜率与日收益序列的周期胜率不是同一指标。

## 10. `costs`

| 字段 | 类型 | 必填 |
|---|---|---:|
| `date` | date | 是 |
| `commission` | number | 是 |
| `tax` | number | 是 |
| `slippage_cost` | number | 是 |
| `market_impact_cost` | number | 否 |
| `borrow_cost` | number | 否 |
| `total_cost` | number | 是 |

## 11. `exposures`

推荐使用长表：

| 字段 | 类型 | 说明 |
|---|---|---|
| `date` | date | 交易日期 |
| `exposure_type` | enum | industry/factor/market_cap |
| `name` | string | 行业或因子名称 |
| `portfolio_value` | number/null | 组合暴露 |
| `benchmark_value` | number/null | 基准暴露 |
| `active_value` | number/null | 主动暴露 |

## 12. `events`

用于审计非普通成交事件：

| 字段 | 类型 | 说明 |
|---|---|---|
| `timestamp` | datetime | 事件时间 |
| `event_type` | enum | rebalance/stop_loss/suspension/limit_up/limit_down/delist |
| `symbol` | string/null | 涉及证券 |
| `rule_id` | string/null | 触发规则 |
| `details` | object | 结构化事件详情 |

## 13. 数据不变量

```text
net_portfolio_value = cash + positions_value

abs(sum(position_weight) + cash_ratio - 1) < tolerance

total_cost = commission + tax + slippage_cost + optional_costs

filled_quantity <= requested_quantity

gross_portfolio_value >= 0
net_portfolio_value >= 0
```

若支持杠杆、做空或破产场景，需要通过策略类型显式放宽对应不变量。

## 14. 输出规则

- 收益率使用小数。
- 日期按升序排列。
- 无法计算的标量使用 `null`。
- 空序列使用 `[]`。
- 禁止输出 `NaN`、`Infinity` 和 `-Infinity`。
- 金额字段不得与比例字段混用。
- 回测失败时不得伪造空的成功结果。

## 15. 验收标准

- `portfolio_daily` 可以复算策略净值。
- `orders` 与 `fills` 可以追踪所有未成交和部分成交情况。
- 毛净值差可以由成本数据解释。
- 每个止损卖出可以追踪到对应规则和事件。
- 指标引擎不需要重新访问撮合引擎内部状态。
- 所有记录带有足够的版本信息以支持复现。

