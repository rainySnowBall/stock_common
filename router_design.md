# Finance Agent Router Design

## 1. 目标

本模块用于金融 Agent 的任务路由（Router）。

Router 的职责不是直接回答用户问题，也不负责执行回测，而是把用户自然语言请求转换为稳定、可评估、可扩展的任务类型，并给出置信度及必要的路由元信息。

当前阶段主要支持两类核心能力：

1. `chat`：日常金融对话、查询、解释类任务。
2. `factor_research`：因子研究、因子有效性分析、策略回测类任务。

为了降低误分类风险，并为后续扩展预留空间，额外保留：

3. `mixed`：一个请求同时包含查询和因子研究/回测。
4. `unknown`：无法可靠归类，交给 LLM Router 或人工策略进一步处理。

Router 建议采用：

```text
Rule Router
    ↓
Classification Model
    ↓
Confidence Gate
    ├── high confidence → direct route
    └── low confidence  → LLM Task Parser
```

即：

> 分类模型负责高频、明确任务的低成本快速路由；LLM 负责模糊意图、混合任务及参数解析。

---

# 2. Router 在 Harness 中的位置

```text
User Input
    │
    ▼
┌─────────────────────┐
│     Rule Router     │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Classification Model│
└──────────┬──────────┘
           │
           ▼
     Confidence Gate
       /          \
      /            \
 High Confidence   Low Confidence
      │                 │
      ▼                 ▼
 Direct Route      LLM Task Parser
      │                 │
      └────────┬────────┘
               ▼
            TaskSpec
               │
      ┌────────┴────────┐
      ▼                 ▼
  Chat Agent      Factor Workflow
```

Router 不应该直接依赖：

- 数据库
- 行情系统
- 回测引擎
- 因子计算模块

Router 只负责：

```text
Natural Language
        ↓
Task Type
+
Confidence
+
Optional Routing Metadata
```

---

# 3. 分类任务定义

## 3.1 一级标签

```text
chat
factor_research
mixed
unknown
```

### chat

适用于不需要历史回测流程的金融查询。

典型任务：

- 股票行情查询
- 财务数据查询
- 新闻查询
- 公司基本面解释
- 行业信息查询
- 技术指标解释
- 金融知识问答
- 指标计算方法解释
- 财报科目解释

示例：

```text
宁德时代最近一个季度的毛利率是多少？
```

```text
MACD 的 DIF 和 DEA 有什么区别？
```

```text
贵州茅台今天为什么跌？
```

输出：

```json
{
  "label": "chat"
}
```

---

### factor_research

适用于需要进行历史数据分析、因子有效性检验或策略回测的请求。

典型任务：

- 单因子有效性分析
- IC / RankIC
- ICIR
- 因子分组收益
- 多空组合分析
- 因子衰减
- 因子换手率
- 策略回测
- 多因子组合
- 股票筛选策略历史表现
- 技术指标策略历史检验

示例：

```text
测试一下过去十年 ROE 因子是否有效。
```

```text
回测 PE 最低的 20% 股票。
```

```text
看看 MACD 金叉策略在沪深300成分股上的表现。
```

输出：

```json
{
  "label": "factor_research"
}
```

---

### mixed

用户一个请求中同时存在：

```text
查询 / 解释
+
因子研究 / 回测
```

例如：

```text
先看一下宁德时代目前的 ROE，再帮我测试过去十年高 ROE 股票有没有超额收益。
```

此时不能简单路由到单一 Agent。

推荐输出：

```json
{
  "label": "mixed"
}
```

然后交给 LLM Task Parser 拆解为：

```text
Task 1 → chat
Task 2 → factor_research
```

---

### unknown

适用于：

- 非金融任务
- 表述严重不完整
- 分类模型无法可靠判断
- 新型任务类别
- 超出当前系统能力范围

例如：

```text
帮我弄一下这个。
```

```text
写一首诗。
```

输出：

```json
{
  "label": "unknown"
}
```

`unknown` 一般不直接终止任务，而是进入：

```text
LLM Router
```

进一步判断。

---

# 4. 分类模型输入设计

## 4.1 基础输入

模型最小输入：

```json
{
  "text": "用户原始输入"
}
```

例如：

```json
{
  "text": "帮我回测过去五年低PE股票的表现"
}
```

---

## 4.2 推荐输入字段

实际工程建议：

```json
{
  "text": "用户当前输入",
  "previous_intent": "chat",
  "conversation_summary": "此前正在讨论贵州茅台估值",
  "language": "zh"
}
```

其中只有：

```text
text
```

是必须项。

其他字段属于 optional context。

推荐原则：

> 分类模型尽量保持短上下文，避免把完整 Conversation History 输入分类模型。

原因：

- 增加 latency
- 增加推理成本
- 引入无关信息
- 增加分类漂移

推荐最多输入：

```text
当前 query
+
最近一次 intent
+
极短 conversation summary
```

---

# 5. 模型输出参数

推荐分类模型标准输出：

```json
{
  "label": "factor_research",
  "confidence": 0.97,
  "probabilities": {
    "chat": 0.01,
    "factor_research": 0.97,
    "mixed": 0.01,
    "unknown": 0.01
  },
  "model_version": "router-v1"
}
```

## 必需参数

### label

```text
chat
factor_research
mixed
unknown
```

---

### confidence

范围：

```text
0.0 ~ 1.0
```

含义：

```text
预测标签对应的概率
```

例如：

```text
factor_research = 0.94
```

则：

```json
{
  "label": "factor_research",
  "confidence": 0.94
}
```

---

## 推荐参数

### probabilities

保存所有类别概率。

例如：

```json
{
  "chat": 0.42,
  "factor_research": 0.48,
  "mixed": 0.08,
  "unknown": 0.02
}
```

这个结果虽然：

```text
factor_research
```

概率最高，但模型明显不确定。

因此应该交给：

```text
LLM Router
```

而不是直接执行。

---

### model_version

例如：

```text
router-v1
router-v1.1
router-v2
```

方便：

- AB Test
- 回溯误分类
- 模型升级
- Offline Eval

---

# 6. Classification Model 参数要求

推荐第一版使用轻量文本分类模型。

可选模型：

```text
MiniLM
BGE-small
DistilBERT
MacBERT
RoBERTa-small
ModernBERT-small
```

对于中文金融输入，推荐优先测试：

```text
MacBERT / Chinese RoBERTa
```

或：

```text
Embedding Model
+
Linear / MLP Classifier
```

---

## 6.1 最大输入长度

推荐：

```text
max_length = 128
```

可接受：

```text
128 ~ 256 tokens
```

Router 通常不需要：

```text
512+
```

长文本。

对于长输入：

```text
取用户最后一个 query
+
必要上下文摘要
```

---

## 6.2 输出类别数

V1：

```text
num_labels = 4
```

对应：

```text
0 = chat
1 = factor_research
2 = mixed
3 = unknown
```

---

## 6.3 Loss

基础：

```text
Cross Entropy Loss
```

如果类别极不均衡，使用：

```text
Weighted Cross Entropy
```

例如：

```python
weights = {
    "chat": 1.0,
    "factor_research": 1.2,
    "mixed": 2.0,
    "unknown": 1.5
}
```

原因：

`mixed` 一般样本较少，但误分类成本较高。

也可以尝试：

```text
Focal Loss
```

适用于：

```text
大量简单样本
+
少量困难样本
```

---

# 7. 置信度策略

不要简单使用：

```text
argmax
```

直接决定路由。

必须增加：

```text
Confidence Gate
```

推荐初始阈值：

```text
confidence >= 0.90
→ direct route

0.70 <= confidence < 0.90
→ LLM Router

confidence < 0.70
→ LLM Router / unknown
```

---

## 7.1 Probability Margin

除了 confidence，还建议计算：

```text
margin = P(top1) - P(top2)
```

例如：

```text
chat            0.52
factor_research 0.44
```

虽然：

```text
top1 = chat
```

但：

```text
margin = 0.08
```

说明任务模糊。

推荐：

```text
margin >= 0.30
```

才允许直接路由。

因此：

```python
if confidence >= 0.90 and margin >= 0.30:
    direct_route()
else:
    llm_route()
```

---

# 8. 推荐最终 Gate 策略

```python
def route_by_classifier(result):

    if result.label in {"mixed", "unknown"}:
        return "LLM_ROUTER"

    if result.confidence < 0.90:
        return "LLM_ROUTER"

    probabilities = sorted(
        result.probabilities.values(),
        reverse=True
    )

    margin = probabilities[0] - probabilities[1]

    if margin < 0.30:
        return "LLM_ROUTER"

    return result.label
```

---

# 9. Rule Router

分类模型之前建议增加一个非常轻量的 Rule Router。

目的不是代替模型，而是：

```text
降低简单任务成本
+
提高显式回测请求准确率
```

---

## 9.1 强回测关键词

例如：

```text
回测
因子有效性
RankIC
IC
ICIR
分组收益
多空收益
历史表现
策略收益
年化收益
最大回撤
夏普
调仓
换手率
```

例如：

```text
帮我回测一下ROE
```

可以直接：

```text
factor_research
```

---

## 9.2 强 Chat 关键词

例如：

```text
是什么
什么意思
怎么计算
解释一下
最新价格
财报
毛利率
净利润
营业收入
新闻
公告
为什么上涨
为什么下跌
```

但 Chat Rule 不应该过强。

例如：

```text
PE是什么意思，以及低PE策略过去十年有没有用？
```

同时包含：

```text
解释
+
回测
```

所以发现因子研究关键词时：

```text
factor_research / mixed
```

优先级应高于简单 Chat Rule。

---

# 10. LLM Fallback 条件

以下情况强制进入 LLM Router：

### Condition 1

```text
confidence < threshold
```

### Condition 2

```text
top1 - top2 < margin_threshold
```

### Condition 3

模型预测：

```text
mixed
```

### Condition 4

模型预测：

```text
unknown
```

### Condition 5

出现多个明显任务动作。

例如：

```text
查一下宁德时代PE，然后回测低PE策略
```

### Condition 6

分类结果与 Rule Router 冲突。

例如：

```text
Rule → factor_research

Classifier → chat
```

则：

```text
LLM Router
```

---

# 11. 训练数据 Schema

训练数据建议：

```json
{
  "id": "route_000001",
  "text": "测试过去十年ROE因子的表现",
  "label": "factor_research",
  "source": "synthetic",
  "difficulty": "easy"
}
```

推荐增加：

```json
{
  "id": "route_000002",
  "text": "低估值这个东西过去几年到底还有没有用？",
  "label": "factor_research",
  "source": "real",
  "difficulty": "hard",
  "notes": "没有出现显式回测关键词"
}
```

字段：

| 字段 | 必须 | 含义 |
|---|---:|---|
| id | 是 | 样本ID |
| text | 是 | 用户输入 |
| label | 是 | 标签 |
| source | 建议 | real / synthetic |
| difficulty | 建议 | easy / medium / hard |
| notes | 否 | 标注说明 |

---

# 12. 数据集构成要求

不要只生成这种简单样本：

```text
帮我回测ROE
```

Router 真正难的是边界情况。

推荐数据比例：

```text
Easy    40%
Medium  35%
Hard    25%
```

---

## Easy

```text
什么是ROE？
```

→ chat

```text
回测ROE因子。
```

→ factor_research

---

## Medium

```text
过去几年ROE这个指标表现怎么样？
```

→ factor_research

```text
宁德时代ROE为什么下降？
```

→ chat

关键区别：

```text
个股当前/历史解释
vs
横截面因子有效性研究
```

---

## Hard

```text
ROE是不是越来越没用了？
```

可能是：

```text
factor_research
```

---

```text
高ROE公司真的比低ROE公司涨得好吗？
```

→ factor_research

---

```text
茅台的PE是多少？低PE股票过去10年收益又怎么样？
```

→ mixed

Hard Sample 是决定 Router 能不能真正上线的关键。

---

# 13. 特别需要构建的 Hard Negative

金融领域很多词同时出现在 Chat 和 Research 中。

例如：

```text
ROE
PE
PB
MACD
RSI
Momentum
估值
盈利能力
```

不能出现：

```text
只要出现 ROE → factor_research
```

必须构造 Hard Negative。

例如：

```text
ROE怎么计算？
```

标签：

```text
chat
```

```text
贵州茅台ROE是多少？
```

标签：

```text
chat
```

```text
过去十年高ROE策略有没有超额收益？
```

标签：

```text
factor_research
```

---

# 14. Chat / Factor Research 判别原则

核心判断不是：

```text
有没有出现因子名字
```

而是：

> 是否需要基于历史横截面/时间序列数据进行系统性研究或策略检验。

因此：

```text
MACD怎么算？
```

→ chat

```text
宁德时代MACD是多少？
```

→ chat

```text
MACD金叉过去十年胜率是多少？
```

→ factor_research

---

# 15. Mixed 判别规则

如果同时满足：

```text
Information Query
+
Historical Research
```

则：

```text
mixed
```

例如：

```text
先告诉我茅台当前PE，再测试低PE策略。
```

---

另一类 Mixed：

```text
Company Analysis
+
Factor Research
```

例如：

```text
分析宁德时代ROE变化，同时看看ROE因子在新能源行业有没有效果。
```

---

# 16. 评估指标

不要只看：

```text
Accuracy
```

至少需要：

```text
Accuracy
Macro Precision
Macro Recall
Macro F1
Per-Class Precision
Per-Class Recall
Confusion Matrix
Fallback Rate
Direct Route Error Rate
```

---

## 16.1 最重要指标

生产环境最重要的其实不是 Accuracy，而是：

```text
Direct Route Error Rate
```

定义：

```text
模型高置信度直接路由
但路由错误
```

例如：

```text
confidence >= 0.90
```

的 10000 个请求中：

```text
50个路由错误
```

则：

```text
Direct Route Error Rate = 0.5%
```

这个指标应该尽量低。

建议目标：

```text
< 1%
```

理想：

```text
< 0.5%
```

---

# 17. Recall 要求

`factor_research` 的 Recall 建议优先保证。

原因：

如果：

```text
factor_research
```

被误路由成：

```text
chat
```

Chat Agent 可能直接凭语言回答：

```text
这个因子长期来看有效……
```

而没有真正执行回测。

这是严重错误。

因此建议：

```text
factor_research recall >= 97%
```

宁可多进入一次：

```text
LLM fallback
```

也不要漏掉真正需要回测的任务。

---

# 18. Offline Evaluation Set

建议建立独立：

```text
router_eval.jsonl
```

禁止加入训练集。

建议 V1：

```text
500 ~ 1000 samples
```

组成：

```text
chat             300
factor_research  300
mixed            150
unknown          100
hard cases       150+
```

其中 hard cases 可以和类别部分重叠统计。

---

# 19. Online Logging

每一次路由建议保存：

```json
{
  "request_id": "req_xxx",
  "text": "ROE这个东西过去几年还有用吗？",
  "rule_result": null,
  "classifier_label": "factor_research",
  "classifier_confidence": 0.81,
  "llm_label": "factor_research",
  "final_route": "factor_research",
  "model_version": "router-v1",
  "latency_ms": 15
}
```

如果最终用户行为证明：

```text
route错误
```

则保存：

```json
{
  "correct_label": "chat"
}
```

这些数据以后就是训练 Router V2 的核心数据来源。

---

# 20. Router API

推荐统一接口：

```python
class Router:

    async def route(
        self,
        text: str,
        context: RouterContext | None = None
    ) -> RouteDecision:
        ...
```

---

## RouterContext

```python
class RouterContext(BaseModel):

    previous_intent: str | None = None

    conversation_summary: str | None = None
```

---

## RouteDecision

```python
class RouteDecision(BaseModel):

    label: Literal[
        "chat",
        "factor_research",
        "mixed",
        "unknown"
    ]

    confidence: float

    source: Literal[
        "rule",
        "classifier",
        "llm"
    ]

    requires_llm_parser: bool

    model_version: str | None = None
```

---

# 21. 推荐运行逻辑

```python
async def route(text, context=None):

    rule_result = rule_router.route(text)

    if rule_result.is_strong_match:
        return rule_result

    cls_result = classifier.predict(text)

    if cls_result.label in {"mixed", "unknown"}:
        return await llm_router.route(text, context)

    if cls_result.confidence < 0.90:
        return await llm_router.route(text, context)

    if cls_result.margin < 0.30:
        return await llm_router.route(text, context)

    return RouteDecision(
        label=cls_result.label,
        confidence=cls_result.confidence,
        source="classifier",
        requires_llm_parser=False,
        model_version=cls_result.model_version
    )
```

---

# 22. V1 推荐配置

```yaml
router:

  rule_router:
    enabled: true

  classifier:
    model: chinese-roberta
    max_length: 128

  labels:
    - chat
    - factor_research
    - mixed
    - unknown

  confidence:
    direct_route_threshold: 0.90
    margin_threshold: 0.30

  llm_fallback:
    enabled: true

  telemetry:
    log_probabilities: true
    log_latency: true
    log_model_version: true
```

---

# 23. 后续标签扩展

当前不要一开始设计过多分类。

V1：

```text
chat
factor_research
mixed
unknown
```

系统稳定后可以扩展为：

```text
chat
    ├── market_query
    ├── fundamental_query
    ├── news_query
    └── knowledge_query

research
    ├── factor_research
    ├── strategy_backtest
    ├── portfolio_analysis
    └── stock_screen

mixed

unknown
```

推荐使用：

```text
Hierarchical Routing
```

即：

```text
Level 1

chat
research
mixed
unknown

        ↓

Level 2

chat
 ├── market
 ├── fundamental
 └── information

research
 ├── factor
 ├── strategy
 └── portfolio
```

不要直接做：

```text
20~50类 Flat Classification
```

否则：

- 数据需求急剧增加
- 类别边界变模糊
- 误分类率提高
- 新功能扩展困难

---

# 24. 模型选择策略

## V1

如果数据量：

```text
< 5,000
```

建议：

```text
Embedding
+
Logistic Regression / Linear Classifier
```

优势：

- 快
- 便宜
- 易调试
- 易部署
- 容易看概率

---

## V2

数据量：

```text
5,000 ~ 50,000
```

可以 fine-tune：

```text
MacBERT
Chinese RoBERTa
MiniLM
```

---

## V3

大量线上数据后：

```text
Small Transformer
+
Knowledge Distillation
```

将：

```text
LLM Router
```

作为 Teacher。

形成：

```text
LLM Teacher
      ↓
Route Dataset
      ↓
Small Classifier
```

这通常比人工从零标大量数据更现实。

---

# 25. Latency 要求

Router 本身不能成为系统性能瓶颈。

建议：

```text
Rule Router
P95 < 1 ms

Classifier
P95 < 30 ms（本地）

整体 Direct Route
P95 < 50 ms
```

如果进入 LLM fallback：

```text
允许更高 latency
```

因为只针对：

```text
模糊请求
```

---

# 26. 模型部署要求

推荐分类模型：

```text
Stateless
```

即：

```text
predict(text)
```

不能依赖 Session 内部隐藏状态。

Session 信息必须显式传递：

```text
RouterContext
```

这样：

- 可水平扩展
- 可测试
- 可复现
- 易于离线评估

---

# 27. 可解释性要求

Router 不需要生成复杂自然语言解释。

但是内部建议记录：

```text
top probabilities
triggered rule
fallback reason
```

例如：

```json
{
  "classifier": {
    "chat": 0.46,
    "factor_research": 0.48
  },
  "fallback_reason": "low_margin"
}
```

这样更方便调试。

---

# 28. Failure Policy

Router 出错时不能让整个 Harness 崩溃。

例如分类模型不可用：

```text
Classifier Exception
        ↓
LLM Router
```

LLM Router 不可用：

```text
LLM Exception
        ↓
default = chat
```

但如果存在明确回测关键词：

```text
default = factor_research
```

因此可以：

```python
try:
    cls_result = classifier.predict(text)
except Exception:
    return await llm_router.route(text)
```

---

# 29. 推荐最终结构

```text
                    User
                      │
                      ▼
               Normalize Input
                      │
                      ▼
                 Rule Router
                      │
              strong match?
                /          \
              YES           NO
               │             │
               │             ▼
               │      Classification Model
               │             │
               │      confidence + margin
               │             │
               │       reliable?
               │        /          \
               │      YES           NO
               │       │             │
               │       │         LLM Router
               │       │             │
               └───────┴──────┬──────┘
                              ▼
                        RouteDecision
                              │
                       mixed / unknown?
                         /          \
                       YES           NO
                        │             │
                  LLM Task Parser    │
                        │             │
                        └──────┬──────┘
                               ▼
                            TaskSpec
                               │
               ┌───────────────┴────────────────┐
               ▼                                ▼
          Chat Agent                    Factor Workflow
```

---

# 30. 核心设计原则

## 原则 1

Router 只负责：

```text
Where should this task go?
```

不要承担完整金融语义解析。

---

## 原则 2

分类模型的目标不是：

```text
100% 覆盖所有请求
```

而是：

```text
高置信度覆盖简单请求
```

模糊请求直接：

```text
LLM fallback
```

---

## 原则 3

宁可：

```text
多 fallback
```

不要：

```text
高置信度错误路由
```

---

## 原则 4

`factor_research` 的误判成本高于 `chat`。

重点保护：

```text
factor_research recall
```

---

## 原则 5

分类模型只负责路由。

真正的：

```text
FactorSpec
BacktestSpec
QuerySpec
```

应该由：

```text
Task Parser
```

产生。

---

# 31. V1 最小落地版本

第一版只需要实现：

```text
Router
├── RuleRouter
├── Classifier
├── ConfidenceGate
└── LLMFallback
```

标签：

```text
chat
factor_research
mixed
unknown
```

关键参数：

```yaml
max_length: 128

direct_route_threshold: 0.90

margin_threshold: 0.30

factor_research_recall_target: 0.97

direct_route_error_target: 0.01
```

先收集真实线上数据。

当数据量逐渐增加以后，再考虑：

```text
Fine-tune Classifier
```

而不是一开始投入大量时间训练复杂分类模型。

---

# 32. 一句话总结

这个 Router 的工程目标不是：

> 用分类模型完全替代 LLM。

而是：

> 用一个低延迟、可评估的小模型承接确定性高的请求，把不确定、混合、复杂的请求交给 LLM，从而在准确率、延迟和成本之间取得平衡。
