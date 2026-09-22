import sys
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_common.backtest import FactorResearchPipeline, LLMExtractStrategyParser, compile_from_text
from stock_common.backtest.compiler import HeuristicStrategyParser
from stock_common.backtest.report import BacktestReportGenerator
from stock_common.backtest.schemas import Operator, SortDirection, StrategyStatus
from stock_common.backtest.schemas import RebalanceFrequency
from stock_common.backtest.stock_resolver import StockSymbolResolver


class FakeExtractionClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = []

    def complete_sync(self, messages):
        self.calls.append(tuple(messages))
        return self.content


class BacktestPipelineTests(unittest.TestCase):
    def test_design_example_compiles_filter_and_market_cap_ranking(self) -> None:
        draft, validation, plan = compile_from_text(
            "从沪深300里选择ROE大于15%、市值最小的20只股票，每月调仓，回测过去十年"
        )

        self.assertEqual(draft.status, StrategyStatus.VALID)
        self.assertTrue(validation.is_valid)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.filters[0].factor, "roe_ttm")
        self.assertEqual(plan.filters[0].value, 0.15)
        self.assertEqual(plan.ranking[0].factor, "market_cap")
        self.assertEqual(plan.ranking[0].direction, SortDirection.ASC)
        self.assertEqual(plan.selection.count, 20)

    def test_equivalent_natural_language_has_same_strategy_hash(self) -> None:
        _, _, first_plan = compile_from_text(
            "从沪深300里选择ROE大于15%、市值最小的20只股票，每月调仓，回测过去十年"
        )
        _, _, second_plan = compile_from_text(
            "过去10年，每月调仓，从沪深300选20只ROE超过15%且总市值最低的股票"
        )

        self.assertIsNotNone(first_plan)
        self.assertIsNotNone(second_plan)
        assert first_plan is not None
        assert second_plan is not None
        self.assertEqual(first_plan.strategy_hash, second_plan.strategy_hash)

    def test_explicit_entry_and_exit_rules_compile(self) -> None:
        draft, validation, plan = compile_from_text(
            "从全A里ROE大于15%买入，ROE低于10%卖出，最多持有20只，每月检查一次，回测过去5年"
        )

        self.assertEqual(draft.status, StrategyStatus.VALID)
        self.assertTrue(validation.is_valid)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.filters, ())
        self.assertEqual(plan.entry_rules[0].factor, "roe_ttm")
        self.assertEqual(plan.entry_rules[0].value, 0.15)
        self.assertEqual(plan.exit_rules[0].factor, "roe_ttm")
        self.assertEqual(plan.exit_rules[0].value, 0.10)
        self.assertEqual(plan.selection.count, 20)

    def test_llm_extract_parser_compiles_structured_json(self) -> None:
        payload = {
            "status": "valid",
            "strategy": {
                "universe": {"name": "all_a_demo"},
                "signals": [],
                "entry_rules": [{"factor": "roe_ttm", "operator": ">", "value": 0.15}],
                "exit_rules": [{"factor": "roe_ttm", "operator": "<", "value": 0.10}],
                "ranking": [{"factor": "roe_ttm", "direction": "desc", "weight": 1.0}],
                "selection": {"count": 20},
                "rebalance": {"frequency": "monthly"},
                "backtest": {"lookback_years": 5, "benchmark": "000300.SH"},
            },
            "unresolved_fields": [],
            "assumptions": [],
            "field_confidence": {"entry_rules[0].factor": 0.96},
        }
        fake_client = FakeExtractionClient(json.dumps(payload, ensure_ascii=False))
        pipeline = FactorResearchPipeline(
            parser=LLMExtractStrategyParser(client=fake_client)
        )

        result = pipeline.compile("ROE大于15%买入，ROE低于10%卖出")

        self.assertEqual(result.status, StrategyStatus.VALID)
        self.assertEqual(len(fake_client.calls), 1)
        self.assertIsNotNone(result.plan)
        assert result.plan is not None
        self.assertEqual(result.plan.entry_rules[0].factor, "roe_ttm")
        self.assertEqual(result.plan.exit_rules[0].value, 0.10)

    def test_llm_extract_parser_uses_factor_map_for_custom_factor(self) -> None:
        payload = {
            "status": "valid",
            "strategy": {
                "universe": {"name": "all_a_demo"},
                "signals": [],
                "entry_rules": [],
                "exit_rules": [],
                "ranking": [
                    {"factor": "earnings_to_price", "direction": "desc", "weight": 1.0}
                ],
                "selection": {"count": 20},
                "backtest": {"lookback_years": 3, "benchmark": "000300.SH"},
            },
            "unresolved_fields": [],
            "assumptions": ["低 PE 映射为 earnings_to_price 从高到低排序。"],
            "field_confidence": {"ranking[0].factor": 0.91},
        }
        fake_client = FakeExtractionClient(json.dumps(payload, ensure_ascii=False))
        pipeline = FactorResearchPipeline(
            parser=LLMExtractStrategyParser(client=fake_client)
        )

        result = pipeline.compile("从全A里选低PE的20只股票，每月调仓，回测过去3年")

        self.assertEqual(result.status, StrategyStatus.VALID)
        self.assertIsNotNone(result.plan)
        assert result.plan is not None
        self.assertEqual(result.plan.ranking[0].factor, "earnings_to_price")
        self.assertEqual(result.plan.rebalance.frequency, RebalanceFrequency.MONTHLY)
        prompt = fake_client.calls[0][1].content
        self.assertIn("真实因子映射表", prompt)
        self.assertIn("earnings_to_price", prompt)

    def test_heuristic_compiles_exact_factor_name_from_full_catalog(self) -> None:
        draft, validation, plan = compile_from_text(
            "从全A里选择quality_composite最高的20只股票，每月调仓，回测过去3年"
        )

        self.assertEqual(draft.status, StrategyStatus.VALID)
        self.assertTrue(validation.is_valid)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.ranking[0].factor, "quality_composite")
        self.assertEqual(plan.ranking[0].direction, SortDirection.DESC)

    def test_heuristic_compiles_chinese_mapping_from_full_catalog(self) -> None:
        draft, validation, plan = compile_from_text(
            "从全A里选择质量综合最高的20只股票，每月调仓，回测过去3年"
        )

        self.assertEqual(draft.status, StrategyStatus.VALID)
        self.assertTrue(validation.is_valid)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.ranking[0].factor, "quality_composite")

    def test_heuristic_compiles_description_match_from_full_catalog(self) -> None:
        draft, validation, plan = compile_from_text(
            "从全A里选择盈利能力比较强的质量因子20只股票，每月调仓，回测过去3年"
        )

        self.assertEqual(draft.status, StrategyStatus.VALID)
        self.assertTrue(validation.is_valid)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.ranking[0].factor, "quality_composite")
        self.assertEqual(len(plan.ranking), 1)

    def test_heuristic_parses_catalog_factor_filter_and_ranking(self) -> None:
        draft, validation, plan = compile_from_text(
            "从全A里选择质量综合大于0.5、股息率最高的20只股票，每月调仓，回测过去3年"
        )

        self.assertEqual(draft.status, StrategyStatus.VALID)
        self.assertTrue(validation.is_valid)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.filters[0].factor, "quality_composite")
        self.assertEqual(plan.filters[0].value, 0.5)
        self.assertEqual(plan.ranking[0].factor, "dividend_yield_3y_avg")

    def test_heuristic_uses_default_variant_for_description_family(self) -> None:
        draft, validation, plan = compile_from_text(
            "从全A里选择收益率标准差最低的20只股票，每月调仓，回测过去3年"
        )

        self.assertEqual(draft.status, StrategyStatus.VALID)
        self.assertTrue(validation.is_valid)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.ranking[0].factor, "return_std_21d")
        self.assertEqual(plan.ranking[0].direction, SortDirection.ASC)

    def test_selection_strategy_without_frequency_defaults_to_monthly(self) -> None:
        draft, validation, plan = compile_from_text(
            "从全A里选择ROE最高的20只股票，回测过去3年"
        )

        self.assertEqual(draft.status, StrategyStatus.VALID)
        self.assertTrue(validation.is_valid)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.rebalance.frequency, RebalanceFrequency.MONTHLY)

    def test_single_stock_pe_percentile_strategy_compiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "market.sqlite3"
            _write_stock_basic(db_path)
            parser = HeuristicStrategyParser(
                stock_resolver=StockSymbolResolver(database_path=db_path)
            )
            pipeline = FactorResearchPipeline(parser=parser)

            result = pipeline.compile("回测 中国卫星 PE后20%买入，PE前20%卖出，每月调仓，时间是2年")

            self.assertEqual(result.status, StrategyStatus.VALID)
            self.assertIsNotNone(result.plan)
            assert result.plan is not None
            self.assertEqual(result.plan.universe.name, "single_stock")
            self.assertEqual(result.plan.universe.symbols, ("600118.SH",))
            self.assertEqual(result.plan.selection.count, 1)
            self.assertEqual(result.plan.entry_rules[0].factor, "earnings_to_price")
            self.assertEqual(result.plan.entry_rules[0].unit, "percentile")
            self.assertEqual(result.plan.entry_rules[0].operator, Operator.GREATER_EQUAL)
            self.assertAlmostEqual(result.plan.entry_rules[0].value, 0.8)
            self.assertEqual(result.plan.exit_rules[0].operator, Operator.LESS_EQUAL)
            self.assertAlmostEqual(result.plan.exit_rules[0].value, 0.2)

    def test_signal_rules_without_fixed_frequency_default_to_triggered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "market.sqlite3"
            _write_stock_basic(db_path)
            parser = HeuristicStrategyParser(
                stock_resolver=StockSymbolResolver(database_path=db_path)
            )
            pipeline = FactorResearchPipeline(parser=parser)

            result = pipeline.compile(
                "回测 下 中国卫星 PE 前20% 卖出 后20% 买入的策略， 时间是2年"
            )

            self.assertEqual(result.status, StrategyStatus.VALID)
            self.assertIsNotNone(result.plan)
            assert result.plan is not None
            self.assertEqual(result.plan.rebalance.frequency, RebalanceFrequency.TRIGGERED)

    def test_triggered_rebalance_parses_signal_driven_entry_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "market.sqlite3"
            _write_stock_basic(db_path)
            parser = HeuristicStrategyParser(
                stock_resolver=StockSymbolResolver(database_path=db_path)
            )
            pipeline = FactorResearchPipeline(parser=parser)

            result = pipeline.compile(
                "回测中国卫星，PE达到前历史20%卖出，后20%买入，时间是2年"
            )

            self.assertEqual(result.status, StrategyStatus.VALID)
            self.assertIsNotNone(result.plan)
            assert result.plan is not None
            self.assertEqual(result.plan.rebalance.frequency, RebalanceFrequency.TRIGGERED)
            self.assertEqual(result.plan.entry_rules[0].factor, "earnings_to_price")
            self.assertEqual(result.plan.entry_rules[0].operator, Operator.GREATER_EQUAL)
            self.assertAlmostEqual(result.plan.entry_rules[0].value, 0.8)
            self.assertEqual(result.plan.exit_rules[0].operator, Operator.LESS_EQUAL)
            self.assertAlmostEqual(result.plan.exit_rules[0].value, 0.2)

    def test_app_phrase_with_pe_before_sell_and_lower_than_buy_compiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "market.sqlite3"
            _write_stock_basic(db_path)
            parser = HeuristicStrategyParser(
                stock_resolver=StockSymbolResolver(database_path=db_path)
            )
            pipeline = FactorResearchPipeline(parser=parser)

            result = pipeline.compile(
                "中国卫星 过去两年回测 ，位于历史前 20%PE 卖出，低于60%PE买入"
            )

            self.assertEqual(result.status, StrategyStatus.VALID)
            self.assertIsNotNone(result.plan)
            assert result.plan is not None
            self.assertEqual(result.plan.rebalance.frequency, RebalanceFrequency.TRIGGERED)
            self.assertEqual(result.plan.exit_rules[0].operator, Operator.LESS_EQUAL)
            self.assertAlmostEqual(result.plan.exit_rules[0].value, 0.2)
            self.assertEqual(result.plan.entry_rules[0].operator, Operator.GREATER_EQUAL)
            self.assertAlmostEqual(result.plan.entry_rules[0].value, 0.4)

    def test_llm_extract_parser_accepts_single_stock_and_percentile_rules(self) -> None:
        payload = {
            "status": "valid",
            "strategy": {
                "universe": {
                    "name": "single_stock",
                    "symbols": ["600118.SH"],
                    "display_name": "中国卫星",
                },
                "signals": [],
                "entry_rules": [
                    {
                        "factor": "earnings_to_price",
                        "operator": ">=",
                        "value": 0.8,
                        "unit": "percentile",
                    }
                ],
                "exit_rules": [
                    {
                        "factor": "earnings_to_price",
                        "operator": "<=",
                        "value": 0.2,
                        "unit": "percentile",
                    }
                ],
                "ranking": [],
                "backtest": {"lookback_years": 2, "benchmark": "000300.SH"},
            },
            "unresolved_fields": [],
            "assumptions": [],
            "field_confidence": {"universe.symbols": 0.95},
        }
        fake_client = FakeExtractionClient(json.dumps(payload, ensure_ascii=False))
        pipeline = FactorResearchPipeline(parser=LLMExtractStrategyParser(client=fake_client))

        result = pipeline.compile("回测 中国卫星 PE 前20%卖出 后20%买入")

        self.assertEqual(result.status, StrategyStatus.VALID)
        self.assertIsNotNone(result.plan)
        assert result.plan is not None
        self.assertEqual(result.plan.universe.symbols, ("600118.SH",))
        self.assertEqual(result.plan.selection.count, 1)
        self.assertEqual(result.plan.rebalance.frequency, RebalanceFrequency.TRIGGERED)

    def test_llm_extract_parser_falls_back_to_heuristic_on_bad_json(self) -> None:
        fake_client = FakeExtractionClient("not json")
        pipeline = FactorResearchPipeline(
            parser=LLMExtractStrategyParser(client=fake_client)
        )

        result = pipeline.compile(
            "从沪深300里选择ROE大于15%、市值最小的20只股票，每月调仓，回测过去十年"
        )

        self.assertEqual(result.status, StrategyStatus.VALID)
        self.assertIsNotNone(result.plan)

    def test_missing_material_parameters_needs_clarification(self) -> None:
        draft, validation, plan = compile_from_text("回测低 PE 策略")

        self.assertEqual(draft.status, StrategyStatus.NEEDS_CLARIFICATION)
        self.assertEqual(validation.status, StrategyStatus.NEEDS_CLARIFICATION)
        self.assertIsNone(plan)
        self.assertGreaterEqual(len(validation.issues), 3)

    def test_pipeline_suggests_defaults_when_parameters_are_missing(self) -> None:
        result = FactorResearchPipeline().run("回测低 PE 策略")

        self.assertEqual(result.status, StrategyStatus.NEEDS_CLARIFICATION)
        self.assertIn("如果你不确定，可以参考默认组合", result.content)
        self.assertIn("过去 5 年", result.content)
        self.assertIn("选 20 只", result.content)
        self.assertIn("全 A", result.content)

    def test_pipeline_runs_demo_backtest(self) -> None:
        result = FactorResearchPipeline().run(
            "从沪深300里选择ROE大于15%、市值最小的20只股票，每月调仓，回测过去十年"
        )

        self.assertEqual(result.status, StrategyStatus.VALID)
        self.assertIsNotNone(result.plan)
        self.assertIsNotNone(result.facts)
        self.assertIsNotNone(result.metrics)
        self.assertIsNotNone(result.charts)
        assert result.facts is not None
        assert result.metrics is not None
        assert result.charts is not None
        self.assertTrue(result.facts.portfolio_daily)
        self.assertIn("指标计算和图表数据生成", result.content)
        self.assertIn("performance", result.metrics.summary)
        self.assertIn("nav", result.metrics.series)
        self.assertIn("drawdown", result.metrics.series)
        self.assertIn("nav_curve", [chart.chart_id for chart in result.charts.charts])
        self.assertIn(
            "drawdown_curve",
            [chart.chart_id for chart in result.charts.charts],
        )

    def test_report_generator_creates_pdf_for_completed_backtest(self) -> None:
        result = FactorResearchPipeline().run(
            "从沪深300里选择ROE大于15%、市值最小的20只股票，每月调仓，回测过去十年"
        )
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "reports"
            asset_dir = Path(tmp) / "assets"

            report = BacktestReportGenerator(
                output_dir=output_dir,
                asset_dir=asset_dir,
            ).generate(result, task_text="unit test")

            self.assertTrue(report.path.exists())
            self.assertGreater(report.path.stat().st_size, 10_000)
            self.assertEqual(report.url, f"/reports/{report.filename}")

    def test_pipeline_runs_explicit_entry_exit_backtest(self) -> None:
        result = FactorResearchPipeline().run(
            "从全A里ROE大于15%买入，ROE低于10%卖出，最多持有20只，每月检查一次，回测过去5年"
        )

        self.assertEqual(result.status, StrategyStatus.VALID)
        self.assertIsNotNone(result.plan)
        self.assertIsNotNone(result.facts)
        self.assertIn("入场规则：ROE_TTM 大于 15%", result.content)
        self.assertIn("出场规则：ROE_TTM 小于 10%", result.content)


def _write_stock_basic(db_path: Path) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE stock_basic (
                ts_code TEXT PRIMARY KEY,
                symbol TEXT,
                name TEXT,
                list_status TEXT
            );
            INSERT INTO stock_basic VALUES ('600118.SH', '600118', '中国卫星', 'L');
            """
        )
        conn.commit()


if __name__ == "__main__":
    unittest.main()
