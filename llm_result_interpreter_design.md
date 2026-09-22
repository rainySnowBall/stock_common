# LLM 回测结果解释模块设计

> 文档版本：v1.0  
> 输入：`MetricsResult`、`StrategyAssessment`、受限元数据  
> 输出：`InterpretationResult`

## 1. 目标

LLM Interpreter 将结构化指标和确定性评价结果转换为清晰、可追溯的自然语言说明。

LLM 只负责解释，不负责：

- 重新计算指标。
- 修改评分、评级或警告。
- 根据图像目测数值。
- 在事实数据之外推断策略未来收益。
- 输出确定性的买入、卖出或收益保证。

## 2. 输入最小化

默认只向 LLM 提供：

```json
{
  "strategy_summary": {},
  "backtest_metadata": {},
  "key_metrics": {},
  "assessment": {},
  "evidence": [],
  "warnings": [],
  "allowed_chart_summaries": []
}
```

不建议直接提供全部逐日持仓和成交记录。需要解释特定异常时，再按工具调用或检索补充对应区间。

## 3. 输出结构

```json
{
  "schema_version": "1.0.0",
  "status": "succeeded",
  "headline": "策略具有长期超额收益，但成本和行业集中度较高",
  "summary": "...",
  "sections": [
    {
      "section_id": "risk_adjusted",
      "title": "风险收益表现",
      "content": "...",
      "evidence_refs": [
        "summary.risk.sharpe_ratio",
        "summary.risk.max_drawdown"
      ]
    }
  ],
  "risk_disclosure": "回测结果不代表未来表现。",
  "unsupported_claims": []
}
```

## 4. 解释顺序

建议固定为：

1. 回测是否可信。
2. 收益是否跑赢基准。
3. 最大回撤和风险调整收益如何。
4. 表现是否稳定、是否存在衰减。
5. 交易成本和成交约束影响多大。
6. 风险是否集中于行业、个股或市值风格。
7. 策略收益主要来自什么。
8. 当前最重要的限制和下一步验证是什么。

## 5. 证据绑定

Evaluation Engine 提供结论模板和证据引用：

```json
{
  "conclusion_id": "WEAKNESS_COST_DRAG",
  "message_key": "high_transaction_cost_drag",
  "severity": "warning",
  "metric_refs": [
    "summary.trading.transaction_cost_drag",
    "summary.trading.annual_turnover"
  ],
  "rule_id": "TR_COST_003"
}
```

LLM 生成的相关句子必须保留：

```json
{
  "text": "高换手和交易成本明显侵蚀了策略收益。",
  "evidence_refs": [
    "summary.trading.transaction_cost_drag",
    "summary.trading.annual_turnover"
  ]
}
```

## 6. 数值规则

- 数值只能从输入字段复制。
- 不允许 LLM 自行做加减乘除得到新指标。
- 百分比展示由格式化函数完成，不交给自由文本模型。
- 小数位和货币单位在输入中预格式化，或通过受控模板渲染。
- 输入为 `null` 时必须表述为“无法计算”，不能写成 0。

建议输入同时提供：

```json
{
  "raw_value": 0.15234,
  "display_value": "15.23%",
  "unit": "percentage"
}
```

## 7. Prompt 约束

系统提示词至少包含：

```text
1. 只能使用输入 JSON 中的事实、指标、评分和警告。
2. 不得重新计算或修正数值。
3. 不得改变 rating、total_score 或 validity.status。
4. 每个实质性结论必须带 evidence_refs。
5. 不得把相关性描述成因果关系。
6. 不得承诺未来收益。
7. 当 validity.status != pass 时，必须首先声明回测结果不可信。
8. 信息不足时明确说明，不得猜测。
```

## 8. 模板与 LLM 的分工

适合固定模板：

- 指标卡标题和数值。
- 回测区间、基准、手续费和滑点。
- 评级、分数和维度分数。
- 固定风险披露。
- Validity Gate 失败原因。

适合 LLM：

- 把多个结构化结论组织成连贯摘要。
- 对收益、风险、稳定性和实盘性的关系进行解释。
- 根据已给证据确定解释顺序和篇幅。
- 用适合用户水平的语言说明指标含义。

## 9. 无效回测处理

当：

```text
assessment.validity.status != pass
```

LLM 必须：

1. 首先说明结果不应被用于判断策略优劣。
2. 列出失败的固定规则。
3. 不输出“优秀策略”“值得使用”等评级性语言。
4. 可以解释已有图表，但必须附带数据质量限制。

## 10. 防止幻觉的后处理

LLM 输出后执行：

- JSON Schema 校验。
- 所有 `evidence_refs` 是否存在。
- 输出中的数字是否来自允许数值集合。
- Rating 和总分是否与 Evaluation Engine 一致。
- 是否包含禁止性投资承诺。
- 是否遗漏严重警告。

伪代码：

```python
allowed_numbers = collect_display_values(llm_input)

for number in extract_numbers(llm_output):
    assert number in allowed_numbers

for ref in collect_evidence_refs(llm_output):
    assert resolve_ref(llm_input, ref) is not None

assert llm_output.rating == assessment.rating
```

## 11. 降级方案

当 LLM 调用失败或输出未通过校验时，使用确定性模板：

```text
回测状态：{validity_status}
综合评级：{rating}
年化收益：{annual_return}
年化超额收益：{annual_excess_return}
Sharpe：{sharpe_ratio}
最大回撤：{max_drawdown}
主要优势：{strength_message_keys}
主要风险：{warning_message_keys}
```

LLM 不应成为用户查看回测结果的单点故障。

## 12. Prompt 与模型版本

每次输出记录：

- `model_id`
- `prompt_version`
- `input_schema_version`
- `output_schema_version`
- `temperature`
- `generated_at`

推荐温度设为低值，并使用结构化输出。

## 13. 测试

测试样例至少覆盖：

- 高收益高回撤策略。
- 低收益低风险策略。
- 成本侵蚀严重策略。
- 样本外失效策略。
- 行业高度集中策略。
- Validity Gate 失败策略。
- 多个关键指标为 `null`。
- LLM 输出不存在的数字或证据引用。

## 14. 验收标准

- LLM 不改变任何事实、评分和评级。
- 所有实质性结论都能解析到有效 `evidence_refs`。
- 输出中的数值均来自允许集合。
- 无效回测不会被解释成有效策略。
- LLM 失败时确定性模板仍能生成报告。
- 相同输入允许措辞变化，但不得改变结论含义。

