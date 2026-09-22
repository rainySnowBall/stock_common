# Finance Router 项目交互流程图

入口文件：`run_web.py`

> 注：当前仓库里部分中文 UI/提示文案显示为乱码，疑似历史编码问题。下面按代码调用关系、枚举、接口和数据结构梳理交互流程，不依赖这些文案内容。

## 1. 系统总览

```mermaid
flowchart LR
  U["用户"] --> FE["浏览器前端<br/>static/index.html + app.js"]

  FE -->|GET /| HTTP["FinanceRouterRequestHandler"]
  FE -->|GET /api/health| HTTP
  FE -->|POST /api/session/reset| HTTP
  FE -->|POST /api/chat| HTTP

  HTTP --> SS["SessionStore<br/>内存会话"]
  HTTP --> AG["FinanceAgent<br/>单轮编排器"]

  AG --> RT["Router<br/>规则 + 分类器 + 兜底"]
  AG --> MS["ModelSelector<br/>按任务选模型"]
  AG --> TP["HeuristicTaskParser<br/>拆分 mixed 请求"]
  AG --> DP["TaskDispatcher"]

  DP --> CH["FinanceChatHandler"]
  DP --> FH["FactorResearchTaskHandler"]
  DP --> PH["RoutingPreviewHandler<br/>mixed/unknown 预览"]

  CH -->|普通聊天| LLM["OpenAI-compatible<br/>chat/completions"]
  CH -->|web_search=true| WS["WebSearchClient<br/>外部搜索 API"]

  FH --> FP["FactorResearchPipeline"]
  FP --> SP["LLMExtractStrategyParser<br/>可选 LLM JSON 提取"]
  SP --> HP["HeuristicStrategyParser<br/>规则兜底"]
  FP --> VAL["StrategyValidator"]
  FP --> CP["StrategyCompiler"]
  FP --> BE["SQLiteFactorBacktestEngine"]
  BE --> DB["data/market_data.sqlite3"]
  BE --> TF["FactorFetchClient<br/>Tushare 实时因子"]
  FP --> ME["MetricsEngine"]
  FP --> CE["ChartEngine"]

  AG --> RESP["AgentResponse"]
  HTTP -->|JSON| FE
  FE --> UI["聊天气泡<br/>指标卡<br/>SVG 图表"]
```

## 2. 启动流程

```mermaid
flowchart TD
  A["python run_web.py --host 127.0.0.1 --port 8000"] --> B["计算 ROOT 和 SRC"]
  B --> C["把 ROOT/src 插入 sys.path"]
  C --> D["from stock_common.web.server import main"]
  D --> E["server.main()"]
  E --> F["argparse 读取 --host / --port"]
  F --> G["create_server(host, port)"]
  G --> H["WebAppState()"]
  H --> I["build_agent()"]

  I --> I1["StockSymbolResolver(enable_llm_extract=True)"]
  I1 --> I2["HeuristicStrategyParser(stock_resolver)"]
  I2 --> I3["LLMExtractStrategyParser(fallback_parser, stock_resolver)"]
  I3 --> I4["FactorResearchPipeline(parser, SQLiteFactorBacktestEngine)"]
  I4 --> I5["FinanceAgent(TaskDispatcher handlers)"]

  I5 --> J["绑定 BoundHandler.app_state"]
  J --> K["ThreadingHTTPServer((host, port), BoundHandler)"]
  K --> L["print 本地访问地址"]
  L --> M["serve_forever()"]
  M --> N["KeyboardInterrupt 或 finally server_close()"]
```

关键点：

- `run_web.py` 本身只负责路径和入口跳转。
- `server.build_agent()` 在 Web 启动时组装真实可用的处理器。
- Web 版 `TaskDispatcher` 明确覆盖四类路由：`chat`、`factor_research`、`mixed`、`unknown`。

## 3. 前端交互流程

```mermaid
flowchart TD
  A["页面加载 app.js"] --> B["init()"]
  B --> C["从 localStorage 读取 sessionId"]
  B --> D["从 localStorage 读取 webSearch 开关"]
  B --> E["checkHealth()"]
  B --> F["bindEvents()"]
  B --> G["聚焦输入框"]

  E --> E1["fetch GET /api/health"]
  E1 --> E2{"健康检查成功?"}
  E2 -->|是| E3["显示在线 + sessions 数"]
  E2 -->|否| E4["显示离线"]

  F --> H["提交表单或 Enter"]
  F --> I["点击 prompt-chip 示例问题"]
  F --> J["点击新会话"]
  F --> K["切换联网搜索"]

  H --> L["submitMessage(input.value)"]
  I --> L
  L --> M{"message 非空且非 pending?"}
  M -->|否| Z["忽略"]
  M -->|是| N["隐藏 empty-state"]
  N --> O["appendMessage(user)"]
  O --> P["清空输入框 + setPending(true)"]
  P --> Q["appendTyping()"]
  Q --> R["fetch POST /api/chat<br/>{session_id, message, web_search}"]
  R --> S{"HTTP OK?"}
  S -->|是| T["保存 payload.session_id 到 localStorage"]
  T --> U["移除 typing"]
  U --> V["appendMessage(assistant, payload.message, payload.response)"]
  V --> W["renderBacktestArtifacts(response)"]
  W --> X["updateTopbar(response.status)"]
  X --> Y["checkHealth() + setPending(false)"]
  S -->|否或异常| ER["移除 typing + 显示请求失败 + setPending(false)"]

  J --> J1["resetSession()"]
  J1 --> J2["POST /api/session/reset {session_id}"]
  J2 --> J3["保存新 session_id"]
  J3 --> J4["清空 .message-row"]
  J4 --> J5["恢复 empty-state + 更新会话标签"]

  K --> K1["state.webSearch = checkbox.checked"]
  K1 --> K2["写入 localStorage"]
```

## 4. HTTP 接口分发

```mermaid
flowchart TD
  A["FinanceRouterRequestHandler 收到请求"] --> B{"HTTP 方法"}

  B -->|GET| G["do_GET()"]
  G --> G1{"path == /api/health?"}
  G1 -->|是| G2["返回 {status: ok, sessions: count}"]
  G1 -->|否| G3["_serve_static(path)"]
  G3 --> G4{"path 为空或 /?"}
  G4 -->|是| G5["static/index.html"]
  G4 -->|否| G6["STATIC_DIR/path"]
  G6 --> G7{"是否越过 STATIC_DIR?"}
  G7 -->|是| G8["400 {error: invalid_path}"]
  G7 -->|否| G9{"文件存在?"}
  G9 -->|是| G10["按 mimetype 返回文件"]
  G9 -->|否| G11["fallback 到 index.html"]

  B -->|POST| P["do_POST()"]
  P --> P1{"path"}
  P1 -->|/api/chat| C["_handle_chat()"]
  P1 -->|/api/session/reset| R["_handle_reset()"]
  P1 -->|其他| N["404 {error: not_found}"]

  C --> C1["_read_json()"]
  C1 --> C2["读取 message/session_id/web_search"]
  C2 --> C3{"message 为空?"}
  C3 -->|是| C4["400 {error: message_required}"]
  C3 -->|否| C5["SessionStore.get(session_id)"]
  C5 --> C6["asyncio.run(agent.handle_user_message(...))"]
  C6 --> C7["_send_json({session_id, message, response, session})"]

  R --> R1["_read_json()"]
  R1 --> R2["SessionStore.reset(session_id)"]
  R2 --> R3["_send_json({session_id, session})"]
```

## 5. `/api/chat` 到 Agent 的单轮编排

```mermaid
sequenceDiagram
  autonumber
  participant FE as Frontend app.js
  participant HTTP as FinanceRouterRequestHandler
  participant Store as SessionStore
  participant Agent as FinanceAgent
  participant Router as Router
  participant Selector as ModelSelector
  participant Parser as TaskParser
  participant Dispatcher as TaskDispatcher
  participant Handler as TaskHandler

  FE->>HTTP: POST /api/chat {session_id, message, web_search}
  HTTP->>HTTP: _read_json(), 校验 message
  HTTP->>Store: get(session_id)
  Store-->>HTTP: SessionState
  HTTP->>Agent: handle_user_message(message, session, metadata)
  Agent->>Agent: session.to_router_context()
  Agent->>Agent: session.record_user_message(message)
  Agent->>Router: route(message, router_context)
  Router-->>Agent: RouteDecision
  Agent->>Selector: select(ModelSelectionRequest(text, route_decision))
  Selector-->>Agent: parent ModelSelection
  Agent->>Parser: parse(...) when requires_llm_parser=true
  Parser-->>Agent: TaskSpec[]
  Agent->>Agent: direct_route TaskSpec when requires_llm_parser=false

  loop each TaskSpec
    Agent->>Selector: select task model when needed
    Selector-->>Agent: task ModelSelection
    Agent->>Dispatcher: dispatch(task, context)
    Dispatcher->>Handler: handle(task, context)
    Handler-->>Dispatcher: TaskResult
    Dispatcher-->>Agent: TaskResult
  end

  Agent->>Agent: compose content + aggregate status
  Agent->>Agent: session.record_agent_response(response)
  Agent-->>HTTP: AgentResponse
  HTTP-->>FE: JSON {session_id, message, response, session}
```

## 6. Router 决策细图

```mermaid
flowchart TD
  A["Router.route(text, context)"] --> B["生成 request_id + started_at"]
  B --> C{"rule_router_enabled?"}
  C -->|是| D["RuleRouter.route(text, context)"]
  C -->|否| H{"classifier_enabled?"}
  D --> E{"rule_result.is_strong_match?"}
  E -->|是| F["_decision_from_rule(rule_result)"]
  F --> Z["_finish(): 添加 request_id/latency + logger.log"]
  E -->|否| H

  H -->|否| FB1["_fallback(reason=unknown)"]
  H -->|是| I["classifier.predict(text, context)"]
  I --> IERR{"分类器异常?"}
  IERR -->|是| FB2["_fallback(reason=classifier_error)"]
  IERR -->|否| J["_rule_classifier_conflict()"]
  J --> K{"规则与分类冲突?"}
  K -->|是| FB3["_fallback(reason=rule_classifier_conflict)"]
  K -->|否| L["ConfidenceGate.evaluate(classifier_result)"]
  L --> M{"allow_direct_route?"}
  M -->|否| FB4["_fallback(reason=low_confidence/low_margin/mixed/unknown)"]
  M -->|是| N["RouteDecision(source=classifier, requires_llm_parser=false)"]
  N --> Z

  FB1 --> O{"llm_fallback_enabled 且 llm_router 存在?"}
  FB2 --> O
  FB3 --> O
  FB4 --> O
  O -->|是| P["ConservativeFallbackRouter.route(...)"]
  O -->|否| Q["_failure_default(...)"]
  P --> R{"fallback 抛错?"}
  R -->|是| Q
  R -->|否| Z
  Q --> Z

  Z --> OUT["RouteDecision<br/>label/confidence/source/requires_llm_parser/fallback_reason/metadata"]
```

路由标签含义：

- `chat`：解释、问答、当前信息查询。
- `factor_research`：因子研究、策略回测、收益/风险表现验证。
- `mixed`：同一输入含多意图，后续要拆分成多个任务。
- `unknown`：当前无法可靠判断，需要用户补充。

## 7. 任务拆分与处理器分支

```mermaid
flowchart TD
  A["RouteDecision"] --> B{"requires_llm_parser?"}
  B -->|否| C["生成单个 TaskSpec<br/>label = route_decision.label<br/>metadata.parser = direct_route"]
  B -->|是| D["HeuristicTaskParser.parse(...)"]

  D --> E{"route label == mixed?"}
  E -->|是| F["按分隔词拆成 segments"]
  F --> G["逐段 infer label<br/>factor_research/chat/unknown"]
  E -->|否| H["生成单个 TaskSpec<br/>可能 requires_user_input"]

  C --> I["TaskDispatcher.dispatch()"]
  G --> I
  H --> I

  I --> J{"task.label"}
  J -->|chat| K["FinanceChatHandler"]
  J -->|factor_research| L["FactorResearchTaskHandler"]
  J -->|mixed| M["RoutingPreviewHandler(MIXED)"]
  J -->|unknown| N["RoutingPreviewHandler(UNKNOWN)"]
  J -->|未配置| O["PlaceholderTaskHandler"]

  K --> KR["TaskResult: llm_chat 或 search_grounded_chat"]
  L --> LR["TaskResult: factor_research_pipeline"]
  M --> MR["TaskResult: needs_user_input"]
  N --> NR["TaskResult: needs_user_input"]
  O --> OR["TaskResult: placeholder/failed"]
```

## 8. Chat 分支

```mermaid
flowchart TD
  A["FinanceChatHandler.handle(task, context)"] --> B{"metadata.web_search 为 true?"}

  B -->|否| C["_handle_with_llm()"]
  C --> C1["从 context.model_selection.model 构造 ChatLLMSettings"]
  C1 --> C2["_build_chat_messages()<br/>system + 最近 8 条历史 + 当前 user"]
  C2 --> C3["OpenAICompatibleChatClient.complete()"]
  C3 --> C4{"成功?"}
  C4 -->|是| C5["TaskResult status=completed<br/>metadata.handler=llm_chat"]
  C4 -->|配置缺失| C6["TaskResult needs_user_input<br/>error=chat_llm_config_missing"]
  C4 -->|请求失败| C7["TaskResult failed<br/>error=chat_llm_failed"]

  B -->|是| D["_handle_with_web_search()"]
  D --> D1["WebSearchSettings.from_env()"]
  D1 --> D2["SearchAnswerSynthesizer.answer(query, history)"]
  D2 --> D3["WebSearchClient.search()<br/>to_thread(search_sync)"]
  D3 --> D4["POST configured web-search API"]
  D4 --> D5["extract_search_results()"]
  D5 --> D6["synthesize() 汇总搜索结果"]
  D6 --> D7{"成功?"}
  D7 -->|是| D8["TaskResult completed<br/>metadata.search_results"]
  D7 -->|配置缺失| D9["TaskResult needs_user_input<br/>required_env"]
  D7 -->|请求失败| D10["TaskResult failed<br/>error=web_search_failed"]
```

## 9. 因子研究与回测分支

```mermaid
flowchart TD
  A["FactorResearchTaskHandler.handle(task, context)"] --> B{"pipeline.parser 是 LLMExtractStrategyParser?"}
  B -->|是| C["parser.for_model(context.model_selection.model)"]
  B -->|否| D["沿用 pipeline"]
  C --> E["FactorResearchPipeline.run(task.text)"]
  D --> E

  E --> F["compile(text)"]
  F --> G["parser.parse(text)"]
  G --> G1{"LLM 提取启用?"}
  G1 -->|是| G2["OpenAI-compatible LLM<br/>输出 JSON strategy spec"]
  G2 --> G3{"解析/请求成功?"}
  G3 -->|否| G4["fallback HeuristicStrategyParser"]
  G3 -->|是| G5["_draft_from_payload()"]
  G1 -->|否| G4
  G4 --> G6["DraftStrategySpec"]
  G5 --> G6

  G6 --> H["StrategyValidator.validate(draft)"]
  H --> I{"validation.is_valid?"}
  I -->|否| J["PipelineResult<br/>needs clarification / invalid<br/>附 unresolved fields"]
  I -->|是| K["StrategyCompiler.compile(spec)"]
  K --> L["ExecutionPlan<br/>plan_id + strategy_hash"]
  L --> M["SQLiteFactorBacktestEngine.run(plan)"]
  M --> N{"BacktestFacts.status == succeeded?"}
  N -->|否| O["PipelineResult invalid<br/>render_backtest_failure"]
  N -->|是| P["MetricsEngine.calculate(facts)"]
  P --> Q["ChartEngine.build(metrics)"]
  Q --> R["PipelineResult valid<br/>content + facts + metrics + charts"]
  R --> S["TaskResult completed<br/>metadata 包含 draft/validation/plan/facts/metrics/charts"]
  J --> T["TaskResult needs_user_input"]
  O --> T
```

## 10. SQLite 回测引擎内部流程

```mermaid
flowchart TD
  A["SQLiteFactorBacktestEngine.run(plan)"] --> B{"股票池是否支持?"}
  B -->|否| F0["_failed(UNSUPPORTED_REAL_UNIVERSE)"]
  B -->|是| C{"因子是否存在?"}
  C -->|否| F1["_failed(UNKNOWN_LIVE_FACTOR)"]
  C -->|是| D["_trade_dates(plan)<br/>查询 daily_qfq"]
  D --> E{"交易日数量 >= 2?"}
  E -->|否| F2["_failed(NO_PRICE_DATA)"]
  E -->|是| G["初始化 current_weights/nav/rows/orders/fills/costs"]

  G --> H["_rebalance_dates(dates, frequency)"]
  H --> I["_benchmark_returns(dates, benchmark)"]
  I --> LOOP["遍历每个 trade_date"]

  LOOP --> J{"是否调仓日?"}
  J -->|是| K["signal_date = 前一交易日"]
  K --> L["_select_target_weights(...)"]
  L --> L1["_priced_symbols(signal_date, trade_date)"]
  L1 --> L2["_required_factors(plan)"]
  L2 --> L3["_factor_values(factor, signal_date)"]
  L3 --> L4["先读 daily_basic 派生/直接字段"]
  L4 --> L5{"本地值缺失?"}
  L5 -->|是| L6["FactorFetchClient.factor_value()<br/>Tushare 实时因子"]
  L5 -->|否| L7["使用本地值"]
  L6 --> L8["_percentile_values()"]
  L7 --> L8
  L8 --> L9["过滤 plan.filters"]
  L9 --> L10{"entry/exit rules 存在?"}
  L10 -->|否| L11["_rank_and_select()<br/>按 ranking 打分选前 N"]
  L10 -->|是| L12["保留 survivor<br/>触发 entry 补仓<br/>触发 exit 卖出"]
  L11 --> L13["_equal_weights(selected)"]
  L12 --> L13

  L13 --> M["计算 turnover + transaction cost"]
  M --> N["_records_for_rebalance()<br/>orders/fills/costs/events"]
  J -->|否| O["沿用 current_weights"]
  N --> P["_returns_for_symbols(trade_date, weights)"]
  O --> P
  P --> Q["更新 gross_nav/net_nav/benchmark_nav"]
  Q --> R["追加 PortfolioDaily"]
  R --> S{"调仓日?"}
  S -->|是| T["_position_snapshots()"]
  S -->|否| U["下一交易日"]
  T --> U
  U --> LOOP

  LOOP --> V["遍历结束"]
  V --> W["BacktestFacts(status=succeeded,<br/>portfolio_daily, positions, orders, fills, costs, events, warnings)"]
```

## 11. 响应数据和前端回测图表渲染

```mermaid
flowchart TD
  A["TaskResult.metadata"] --> B{"是否包含 metrics 和 charts?"}
  B -->|否| C["只渲染 assistant markdown 气泡"]
  B -->|是| D["extractBacktestArtifact(response)"]
  D --> E["metrics.summary"]
  E --> F["指标卡<br/>年化收益/年化超额/Sharpe/最大回撤/年换手"]
  D --> G["charts.charts[]"]
  G --> H["chart_id = nav_curve"]
  G --> I["chart_id = drawdown_curve"]
  G --> J["chart_id = monthly_return_heatmap"]
  G --> K["chart_id = annual_return_comparison"]
  H --> L["renderLineChart()"]
  I --> L
  J --> M["renderMonthlyHeatmap()"]
  K --> N["renderAnnualBars()"]
  L --> O["SVG 插入 .chart-panel-mini"]
  M --> O
  N --> O
  F --> P["backtest-dashboard"]
  O --> P
  P --> Q["追加到 assistant bubble"]
```

HTTP 响应结构：

```json
{
  "session_id": "session_xxx",
  "message": "AgentResponse.content",
  "response": {
    "content": "...",
    "status": "completed | needs_user_input | partial | failed",
    "route_decision": {},
    "model_selection": {},
    "tasks": [],
    "task_results": [],
    "metadata": {}
  },
  "session": {
    "session_id": "session_xxx",
    "previous_intent": "chat | factor_research | mixed | unknown",
    "conversation_summary": "...",
    "message_count": 2
  }
}
```

## 12. 会话状态流

```mermaid
flowchart TD
  A["浏览器 localStorage.sessionId"] --> B{"请求带 session_id?"}
  B -->|是| C["SessionStore.get(session_id)"]
  C --> D{"内存中存在?"}
  D -->|是| E["复用 SessionState"]
  D -->|否| F["用传入 session_id 创建新 SessionState"]
  B -->|否| G["创建随机 session_<uuid>"]

  E --> H["record_user_message()"]
  F --> H
  G --> H
  H --> I["messages append user"]
  I --> J["刷新 conversation_summary<br/>最近 4 条消息"]
  J --> K["AgentResponse 生成"]
  K --> L["record_agent_response()"]
  L --> M["previous_intent = route_decision.label"]
  M --> N["messages append assistant"]
  N --> O["返回 session 信息给前端"]
  O --> P["前端保存 session_id 到 localStorage"]

  R["新会话按钮"] --> S["POST /api/session/reset"]
  S --> T["SessionStore.reset(session_id)"]
  T --> U["替换为新的空 SessionState"]
```

## 13. 主要异常与降级路径

```mermaid
flowchart TD
  A["用户请求"] --> B{"异常/边界"}
  B -->|空 message| C["HTTP 400 message_required"]
  B -->|静态路径越界| D["HTTP 400 invalid_path"]
  B -->|未知 POST 路径| E["HTTP 404 not_found"]
  B -->|路由低置信/冲突| F["ConservativeFallbackRouter<br/>requires_llm_parser=true"]
  B -->|聊天 LLM 环境缺失| G["TaskResult needs_user_input<br/>chat_llm_config_missing"]
  B -->|聊天 LLM 请求失败| H["TaskResult failed<br/>chat_llm_failed"]
  B -->|搜索 API 环境缺失| I["TaskResult needs_user_input<br/>web_search_config_missing"]
  B -->|搜索 API 请求失败| J["TaskResult failed<br/>web_search_failed"]
  B -->|策略缺少参数| K["PipelineResult needs_clarification<br/>提示补充股票池/年限/数量等"]
  B -->|回测数据不可用| L["PipelineResult invalid<br/>BacktestFacts.failed"]
  B -->|Dispatcher handler 抛错| M["TaskResult failed<br/>metadata.error_type/error"]
```

## 14. 运行时配置边界

| 能力 | 主要配置 | 使用位置 |
| --- | --- | --- |
| 普通聊天 LLM | `FINANCE_CHAT_*`，以及 fallback `DEEPSEEK_*` / `OPENAI_*` | `ChatLLMSettings.from_model_profile()` |
| 因子研究/任务解析 LLM | `FINANCE_RESEARCH_*` 或强模型配置 | `LLMExtractStrategyParser.for_model()` |
| 是否启用因子 LLM 抽取 | `FACTOR_RESEARCH_PARSER_MODE=llm/json/...` | `LLMExtractStrategyParser._is_enabled()` |
| 个股名称 LLM 抽取 | `STOCK_RESOLVER_LLM_MODE=on/llm/json/...` | `StockIdentityExtractor._is_enabled()` |
| 联网搜索 | `FINANCE_WEB_SEARCH_API_URL`、`FINANCE_WEB_SEARCH_API_KEY` 等 | `WebSearchSettings.from_env()` |
| 本地市场库 | `DATA_FETCH_DB_PATH`，默认 `data/market_data.sqlite3` | `DataFetchConfig.from_env()` |
| 实时因子 | `TUSHARE_TOKEN`、可选 `TUSHARE_HTTP_URL` | `FactorFetchClient` |

## 15. 源码索引

- `run_web.py`：入口，只设置 `src` 路径并调用 Web server `main()`。
- `src/stock_common/web/server.py`：HTTP 服务、session store、Agent 组装、静态资源和 API 分发。
- `src/stock_common/web/static/app.js`：前端状态、事件绑定、API 调用、Markdown/数学/回测图表渲染。
- `src/stock_common/web/handlers.py`：Web 侧 chat/search handler 和 mixed/unknown 预览 handler。
- `src/stock_common/agent/finance_agent.py`：路由、选模型、任务构建、dispatch、响应聚合。
- `src/stock_common/router/router.py`：规则、分类器、置信门控、fallback 的路由编排。
- `src/stock_common/agent/task_parser.py`：mixed 请求拆分和任务标签推断。
- `src/stock_common/backtest/pipeline.py`：自然语言策略到回测结果的端到端 pipeline。
- `src/stock_common/backtest/real_engine.py`：SQLite + Tushare 因子回测执行。
- `src/stock_common/backtest/metrics.py`：收益、风险、交易、稳定性等指标计算。
- `src/stock_common/backtest/charts.py`：图表数据 artifact 生成。
- `src/stock_common/search/web_search.py`：外部搜索 API 客户端。
- `src/stock_common/llm.py`：OpenAI-compatible chat-completions 客户端。
