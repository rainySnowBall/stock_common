# Stock Common / Finance Research

面向金融问答、任务路由、因子研究和本地回测的 Python 项目。当前 Web UI 名称为 **Finance Research**，后端包含路由器、金融聊天处理器、自然语言策略编译器、因子回测引擎、Tushare 数据拉取和测试套件。

## 当前能力

- 金融请求路由：区分普通金融问答、因子研究/回测、混合任务和未知任务。
- Web UI：本地运行后可在浏览器中对话、查看路由结果和回测输出。
- 因子策略解析：支持 LLM JSON 抽取，也支持启发式 fallback。
- 因子目录：从本地 `factor_map.md` 解析 Tushare `factor_value` 因子，支持 factor_name、中文映射和描述词召回。
- 本地行情数据：使用 SQLite 存储 A 股基础信息、前复权日线和 `daily_basic`。
- 实时因子回测：使用 Tushare `factor_value`，并带本地 SQLite 缓存和回测前覆盖率检查。
- 报告与图表：回测成功后生成指标和图表数据，部分流程可生成 PDF 报告。

## 项目结构

```text
src/stock_common/
  agent/          # 单轮编排、任务拆分和任务派发
  backtest/       # 策略编译、回测、指标、图表、报告
  data_fetch/     # Tushare 客户端、SQLite 仓库、调度和因子拉取
  model_selection/# 按任务选择模型配置
  router/         # 规则路由、分类器、置信门控和 fallback
  search/         # 联网检索配置与客户端
  web/            # 本地 Web 服务和静态前端

scripts/
  backfill_daily_basic.py
  generate_factor_map.py
  generate_interaction_flow_pdf.py

tests/            # unittest 测试
```

## 环境要求

- Python 3.11+
- 可选依赖：
  - `pandas`
  - `tushare`

安装项目和数据拉取依赖：

```powershell
pip install -e ".[data-fetch]"
```

如果只跑大部分单元测试，也可以先使用当前环境直接运行 `unittest`。

## 配置

配置模板在 [.env.example](C:/Users/Administrator/Desktop/stock_common/.env.example)。实际运行默认会读取多个 `.env` 位置，其中本项目当前常用文件是：

```text
src/stock_common/.env
```

数据拉取和真实因子回测相关变量：

```text
TUSHARE_TOKEN=你的 token
TUSHARE_HTTP_URL=https://t.xiaodefa.top/
DATA_FETCH_DB_PATH=data/market_data.sqlite3
DATA_FETCH_START_DATE=20100101
DATA_FETCH_STOCK_STATUSES=L,D,P
DATA_FETCH_REQUEST_SLEEP_SECONDS=0.12
DATA_FETCH_RETRY_COUNT=3
```

注意：`DataFetchConfig.from_env()` 当前对 Tushare 数据链路使用 `.env` 优先级，`src/stock_common/.env` 会覆盖系统/进程里的同名变量。

LLM 相关变量包括：

```text
FINANCE_CHAT_FAST_MODEL_NAME=
FINANCE_CHAT_FAST_BASE_URL=
FINANCE_CHAT_FAST_API_KEY=
FINANCE_RESEARCH_BALANCED_MODEL_NAME=
FINANCE_RESEARCH_BALANCED_BASE_URL=
FINANCE_RESEARCH_BALANCED_API_KEY=
```

如果不配置 LLM，因子研究流程会尽量 fallback 到启发式解析器。

## 运行 Web UI

```powershell
python run_web.py --host 127.0.0.1 --port 8000
```

启动后访问：

```text
http://127.0.0.1:8000
```

示例输入：

```text
ROE 是什么？
从全A里选择质量综合最高的20只股票，每月调仓，回测过去3年
先查 PE，再回测低 PE 策略
```

## 数据拉取

运行一次日线和 `daily_basic` 更新：

```powershell
python -m stock_common.data_fetch.scheduler --once
```

指定结束日期：

```powershell
python -m stock_common.data_fetch.scheduler --once --end-date 20260914
```

补齐 `daily_basic`：

```powershell
python scripts/backfill_daily_basic.py --end-date 20260914 --workers 12
```

查询 Tushare 因子列表：

```powershell
python -m stock_common.data_fetch.factor_fetch --list
```

查询某个因子值：

```powershell
python -m stock_common.data_fetch.factor_fetch --factor-name roe_ttm --trade-date 20260914
```

`factor_value` 查询结果会写入本地 SQLite 缓存表：

- `factor_values`
- `factor_value_fetches`

重复查询同一因子、日期或股票时会优先读本地缓存。

## 因子研究和回测

核心入口：

- `stock_common.backtest.compiler.compile_from_text()`
- `stock_common.backtest.pipeline.FactorResearchPipeline`
- `stock_common.backtest.real_engine.SQLiteFactorBacktestEngine`

启发式解析器支持：

- 直接 factor_name，例如 `quality_composite`
- 中文映射，例如“质量综合”“股息率”“收益率标准差”
- 因子描述词召回，例如“盈利能力比较强的质量因子”
- 阈值条件，例如“质量综合大于0.5”
- 排序方向，例如“最高”“最低”“从小到大”

真实回测边界：

- 当前真实引擎支持全 A 和单只股票。
- 沪深300、中证500等指数历史成分股尚未严谨接入，会被拒绝，避免当前成分倒灌历史。
- 财务因子的严格 point-in-time 语义仍取决于 Tushare `factor_value` 的历史口径。
- 回测前会检查因子覆盖率，覆盖率过低会停止运行。

## 测试

运行全量测试：

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

常用局部测试：

```powershell
python -m unittest tests.test_backtest_pipeline
python -m unittest tests.test_real_engine
python -m unittest tests.test_data_fetch
python -m unittest tests.test_web_server
```

当前测试覆盖路由、模型选择、Web 服务、策略编译、因子目录、Tushare 配置、本地缓存和真实回测引擎。

## 常见问题

### 仍然提示 token 不对

先确认实际生效配置：

```powershell
python -c "import sys; from pathlib import Path; sys.path.insert(0, str(Path('src').resolve())); from stock_common.data_fetch.config import DataFetchConfig; c=DataFetchConfig.from_env(); print(bool(c.tushare_token), c.tushare_http_url, c.database_path)"
```

如果 `TUSHARE_HTTP_URL` 是代理地址但仍返回 token 错误，说明请求已经走代理，下一步应检查 `src/stock_common/.env` 里的 `TUSHARE_TOKEN` 是否被代理服务接受。

### Web UI 名称还是旧的

浏览器可能缓存了静态文件。刷新页面或重启本地服务后应显示：

```text
Finance Research
```

## 相关设计文档

- [router_design.md](C:/Users/Administrator/Desktop/stock_common/router_design.md)
- [interaction_flow.md](C:/Users/Administrator/Desktop/stock_common/interaction_flow.md)
- [strategy_compiler_design.md](C:/Users/Administrator/Desktop/stock_common/strategy_compiler_design.md)
- [backtest_metrics.md](C:/Users/Administrator/Desktop/stock_common/backtest_metrics.md)
- [backtest_fact_contract.md](C:/Users/Administrator/Desktop/stock_common/backtest_fact_contract.md)
- [evaluation_pipeline_overview.md](C:/Users/Administrator/Desktop/stock_common/evaluation_pipeline_overview.md)
