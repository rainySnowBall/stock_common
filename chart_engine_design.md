# Chart Engine 图表生成设计

> 文档版本：v1.0  
> 输入：`BacktestFacts`、`MetricsResult`、`StrategyAssessment`  
> 输出：`ChartBundle`

## 1. 职责

Chart Engine 将标准化数据渲染为交互式或静态图表。

负责：

- 图表数据选择、整形、显示格式和视觉样式。
- Plotly、Matplotlib 或 Seaborn 渲染。
- 图表级错误、空状态和导出。
- 图表与指标、评价证据之间的映射。

不负责：

- 重新定义 CAGR、Sharpe、最大回撤等金融公式。
- 从像素或展示字符串反向计算指标。
- 修改 Evaluation Engine 产生的分数和评级。
- 让 LLM 自由生成未经校验的图表代码。

## 2. 技术选型

| Package | 主要用途 |
|---|---|
| `plotly` | Web Dashboard、交互悬停、缩放和筛选 |
| `matplotlib` | 稳定的 PNG、SVG、PDF 静态输出 |
| `seaborn` | 月度收益、参数敏感性等热力图 |
| `pandas` | 图表前的数据透视、重采样和整形 |

建议：

```text
Dashboard 默认：Plotly
静态研究报告：Matplotlib / Seaborn
```

## 3. 输出结构

```json
{
  "schema_version": "1.0.0",
  "chart_schema_version": "1.0.0",
  "run_id": "bt_001",
  "charts": [
    {
      "chart_id": "nav_curve",
      "status": "succeeded",
      "format": "plotly_json",
      "data_ref": "series.nav",
      "metric_refs": [
        "summary.performance.annual_return"
      ],
      "artifact": {}
    }
  ],
  "warnings": []
}
```

## 4. 图表注册表

```python
CHART_REGISTRY = {
    "nav_curve": NavChartBuilder(),
    "drawdown_curve": DrawdownChartBuilder(),
    "monthly_return_heatmap": MonthlyHeatmapBuilder(),
    "annual_return_comparison": AnnualReturnBuilder(),
    "rolling_return_curve": RollingReturnBuilder(),
    "rolling_sharpe_curve": RollingSharpeBuilder(),
    "turnover_curve": TurnoverBuilder(),
    "transaction_cost_chart": CostBuilder(),
    "industry_exposure": IndustryExposureBuilder(),
    "factor_ic_curve": FactorICBuilder(),
}
```

接口：

```python
class ChartBuilder(Protocol):
    chart_id: str
    required_fields: set[str]
    supported_formats: set[str]

    def build(
        self,
        context: ChartContext,
        config: ChartConfig,
    ) -> ChartArtifact:
        ...
```

## 5. 图表优先级

### P0：用户必须看到

| 图表 | 输入 | 主要回答 |
|---|---|---|
| 净值曲线 | 策略、基准、超额净值 | 赚了多少、是否跑赢 |
| 回撤曲线 | 策略与基准回撤 | 中途可能亏多少 |
| 月度收益热力图 | 月度策略收益 | 收益是否稳定 |
| 年度收益对比 | 策略与基准年度收益 | 是否依赖少数年份 |
| Rolling 1Y Return | 滚动收益 | 不同时间窗口是否有效 |
| Rolling Sharpe | 滚动 Sharpe | 是否出现策略衰减 |

### P1：实盘和风险

| 图表 | 输入 |
|---|---|
| Gross NAV vs Net NAV | 毛净值序列 |
| 交易成本拆分 | 佣金、税费、滑点 |
| 换手率曲线 | 日/月换手 |
| 持仓数量与现金 | 持仓数、现金比例 |
| 行业暴露 | 组合、基准与主动行业权重 |
| 市值暴露 | 组合和基准市值分布 |

### P2：因子和归因

| 图表 | 输入 |
|---|---|
| 因子分组收益 | Quantile Return |
| IC / Rank IC | 因子 IC 时间序列 |
| 收益贡献 | 个股、行业和因子贡献 |
| 参数敏感性 | 参数组合与评价指标 |
| 市场环境分段 | 牛、熊、震荡市场表现 |

## 6. 图表与评价维度映射

| 评价维度 | 主要图表 |
|---|---|
| 收益能力 | 净值、年度收益 |
| 风险水平 | 回撤、滚动波动率、尾部收益分布 |
| 风险调整收益 | Rolling Sharpe、Rolling Calmar |
| 稳定性 | 月度热力图、滚动收益、市场环境分段 |
| 实盘可行性 | 毛净值、成本、换手和拒单 |
| 集中度 | 持仓数量、行业和个股权重 |
| 逻辑与归因 | 因子暴露、分组收益、IC 和贡献度 |

## 7. ChartConfig

```json
{
  "theme": "light",
  "locale": "zh-CN",
  "return_format": ".2%",
  "currency": "CNY",
  "default_width": 1200,
  "default_height": 500,
  "output_format": "plotly_json",
  "show_benchmark": true,
  "show_annotations": true,
  "max_points": 5000
}
```

## 8. Plotly 示例

```python
import pandas as pd
import plotly.graph_objects as go


class NavChartBuilder:
    chart_id = "nav_curve"
    required_fields = {
        "date",
        "strategy_nav",
        "benchmark_nav",
        "excess_nav",
    }

    def build(self, data: pd.DataFrame) -> go.Figure:
        fig = go.Figure()

        for field, name in [
            ("strategy_nav", "Strategy"),
            ("benchmark_nav", "Benchmark"),
            ("excess_nav", "Excess"),
        ]:
            fig.add_scatter(
                x=data["date"],
                y=data[field],
                name=name,
                mode="lines",
            )

        fig.update_layout(
            title="Strategy NAV",
            xaxis_title="Date",
            yaxis_title="NAV",
            hovermode="x unified",
        )

        return fig
```

## 9. 热力图示例

```python
import plotly.express as px


def build_monthly_heatmap(monthly_returns):
    matrix = monthly_returns.pivot(
        index="year",
        columns="month",
        values="monthly_return",
    )

    return px.imshow(
        matrix,
        color_continuous_scale="RdYlGn",
        color_continuous_midpoint=0,
        aspect="auto",
        text_auto=".1%",
        title="Monthly Return Heatmap",
    )
```

## 10. 注释规则

图表注释必须来自结构化事实：

- 最大回撤起点、谷底和恢复日。
- 最佳和最差月份。
- 最近滚动 Sharpe 转负日期。
- 毛净值差异超过阈值的时期。
- 评价规则触发的异常区间。

注释对象：

```json
{
  "annotation_id": "max_drawdown_bottom",
  "date": "2022-10-31",
  "value": -0.183,
  "message_key": "maximum_drawdown_bottom",
  "metric_ref": "summary.risk.max_drawdown"
}
```

## 11. 大数据量处理

- 原始事实和指标序列保持完整。
- 只允许在展示层降采样。
- 月度、年度和事件关键点不得被降采样删除。
- 图表降采样不得影响 Summary 指标。
- 导出报告应记录是否使用了展示降采样。

## 12. 空状态与异常

| 场景 | 图表行为 |
|---|---|
| 数据为空 | 返回 `empty` 状态和原因 |
| 必填字段缺失 | 返回 `invalid_input` |
| 样本不足 | 允许显示已有区间并附警告 |
| 单张图渲染失败 | 不阻塞其他图表 |
| Evaluation 无评级 | 仍可展示事实图表，但显示无效回测标识 |

## 13. 可访问性

- 不只依赖红绿颜色表达正负。
- 折线应同时使用颜色、名称和线型区分。
- 提供图表标题、轴名称、单位和数据来源。
- 为导出报告提供简短的确定性图表摘要。
- 百分比、金额和净值必须使用不同格式。

## 14. 缓存

```text
chart_cache_key = hash(
    run_id
    + metrics_version
    + evaluation_profile_version
    + chart_schema_version
    + chart_config
)
```

## 15. 验收标准

- 每张图都由注册表中的固定 Builder 生成。
- Chart Engine 不重新计算核心评价指标。
- 图表数据可以追溯至 Facts 或 Metrics 字段。
- 同一数据和配置产生相同图表数据。
- 交互式与静态图表表达相同的数值口径。
- 单图失败不会导致整份报告失败。

