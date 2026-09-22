"""Task handler protocols and default placeholder handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from stock_common.backtest import FactorResearchPipeline
from stock_common.backtest.llm_extract import LLMExtractStrategyParser
from stock_common.backtest.report import BacktestReportError, BacktestReportGenerator
from stock_common.backtest.schemas import PipelineResult, StrategyStatus, to_serializable
from stock_common.agent.schemas import AgentStatus, TaskExecutionContext, TaskResult, TaskSpec
from stock_common.router.schemas import RouteLabel


class TaskHandler(Protocol):
    """Executor for one parsed task."""

    async def handle(self, task: TaskSpec, context: TaskExecutionContext) -> TaskResult:
        """Run the task and return a structured result."""


@dataclass(frozen=True)
class PlaceholderTaskHandler:
    """Safe default handler used before real workflows are connected."""

    label: RouteLabel

    async def handle(self, task: TaskSpec, context: TaskExecutionContext) -> TaskResult:
        if self.label is RouteLabel.UNKNOWN or task.label is RouteLabel.UNKNOWN:
            return TaskResult(
                task=task,
                content="Unable to determine a supported finance task from this request.",
                status=AgentStatus.NEEDS_USER_INPUT,
                model_selection=context.model_selection,
                metadata={"handler": "placeholder"},
            )

        return TaskResult(
            task=task,
            content=f"{self.label.value} handler is not configured.",
            status=AgentStatus.COMPLETED,
            model_selection=context.model_selection,
            metadata={"handler": "placeholder"},
        )


@dataclass(frozen=True)
class FactorResearchTaskHandler:
    """Run natural-language factor research requests through the backtest pipeline."""

    pipeline: FactorResearchPipeline = FactorResearchPipeline()
    report_generator: BacktestReportGenerator | None = BacktestReportGenerator()

    async def handle(self, task: TaskSpec, context: TaskExecutionContext) -> TaskResult:
        pipeline = self.pipeline
        if isinstance(pipeline.parser, LLMExtractStrategyParser):
            pipeline = FactorResearchPipeline(
                parser=pipeline.parser.for_model(context.model_selection.model),
                validator=pipeline.validator,
                compiler=pipeline.compiler,
                engine=pipeline.engine,
                metrics_engine=pipeline.metrics_engine,
                chart_engine=pipeline.chart_engine,
            )
        result = pipeline.run(task.text)
        metadata = _pipeline_metadata(result)
        content = result.content
        if _can_generate_report(result) and self.report_generator is not None:
            try:
                report = self.report_generator.generate(result, task_text=task.text)
                metadata["report"] = report.to_dict()
                content = f"{content}\n\nPDF 报告已生成，可在回测图表下方下载。"
            except BacktestReportError as exc:
                metadata["report_error"] = str(exc)
        return TaskResult(
            task=task,
            content=content,
            status=_agent_status_for_pipeline(result),
            model_selection=context.model_selection,
            metadata=metadata,
        )


def _agent_status_for_pipeline(result: PipelineResult) -> AgentStatus:
    if result.status is StrategyStatus.VALID:
        return AgentStatus.COMPLETED
    return AgentStatus.NEEDS_USER_INPUT


def _can_generate_report(result: PipelineResult) -> bool:
    return (
        result.status is StrategyStatus.VALID
        and result.plan is not None
        and result.facts is not None
        and result.metrics is not None
        and result.charts is not None
    )


def _pipeline_metadata(result: PipelineResult) -> dict[str, object]:
    metadata: dict[str, object] = {
        "handler": "factor_research_pipeline",
        "pipeline_status": result.status.value,
        "draft": to_serializable(result.draft),
    }
    if result.validation is not None:
        metadata["validation"] = to_serializable(result.validation)
    if result.plan is not None:
        metadata["plan"] = to_serializable(result.plan)
    if result.facts is not None:
        metadata["facts"] = {
            "status": result.facts.status.value,
            "metadata": to_serializable(result.facts.metadata),
            "data_quality": to_serializable(result.facts.data_quality),
            "portfolio_daily_tail": to_serializable(result.facts.portfolio_daily[-3:]),
            "warnings": to_serializable(result.facts.warnings),
        }
    if result.metrics is not None:
        metadata["metrics"] = to_serializable(result.metrics)
    if result.charts is not None:
        metadata["charts"] = to_serializable(result.charts)
    return metadata
