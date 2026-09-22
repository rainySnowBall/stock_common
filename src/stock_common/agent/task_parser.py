"""Task parser interfaces and default heuristic parser."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from stock_common.agent.schemas import SessionState, TaskSpec
from stock_common.model_selection.schemas import ModelSelection
from stock_common.router.config import DEFAULT_CHAT_KEYWORDS, DEFAULT_FACTOR_KEYWORDS
from stock_common.router.normalization import normalize_text
from stock_common.router.schemas import RouteDecision, RouteLabel


class TaskParser(Protocol):
    """Parse one user message into runnable task specs."""

    async def parse(
        self,
        text: str,
        route_decision: RouteDecision,
        model_selection: ModelSelection,
        session: SessionState,
    ) -> tuple[TaskSpec, ...]:
        """Return one or more tasks for downstream dispatch."""


_SPLIT_RE = re.compile(r"[，,。；;]|(?:\bthen\b)|(?:然后)|(?:同时)|(?:并且)|(?:顺便)|(?:以及)|(?:再)")
_LEADING_CONNECTOR_RE = re.compile(r"^(先|再|然后|同时|并且|顺便|以及)\s*")
_FACTOR_PATTERN_RE = re.compile(
    r"(回测|因子|策略|rankic|icir|信息系数|分组收益|多空|超额|年化|最大回撤|夏普|换手|"
    r"过去[一二三四五六七八九十0-9]+年.*(收益|表现|有效|有没有用|胜率))"
)


@dataclass(frozen=True)
class HeuristicTaskParser:
    """Dependency-free parser used until an LLM task parser is wired in."""

    max_tasks: int = 8

    async def parse(
        self,
        text: str,
        route_decision: RouteDecision,
        model_selection: ModelSelection,
        session: SessionState,
    ) -> tuple[TaskSpec, ...]:
        del model_selection, session

        if not route_decision.requires_llm_parser and route_decision.label in {
            RouteLabel.CHAT,
            RouteLabel.FACTOR_RESEARCH,
        }:
            return (
                TaskSpec(
                    task_id="task_001",
                    label=route_decision.label,
                    text=text,
                    metadata={"parser": "direct_route"},
                ),
            )

        if route_decision.label is RouteLabel.MIXED:
            return self._parse_mixed(text)

        return (
            TaskSpec(
                task_id="task_001",
                label=route_decision.label,
                text=text,
                requires_user_input=route_decision.label is RouteLabel.UNKNOWN,
                metadata={"parser": "heuristic", "parent_route": route_decision.label.value},
            ),
        )

    def _parse_mixed(self, text: str) -> tuple[TaskSpec, ...]:
        segments = self._split_segments(text)
        if not segments:
            return (
                TaskSpec(
                    task_id="task_001",
                    label=RouteLabel.MIXED,
                    text=text,
                    requires_user_input=True,
                    metadata={"parser": "heuristic", "parent_route": RouteLabel.MIXED.value},
                ),
            )

        tasks: list[TaskSpec] = []
        for index, segment in enumerate(segments[: self.max_tasks], start=1):
            label = self._infer_label(segment)
            tasks.append(
                TaskSpec(
                    task_id=f"task_{index:03d}",
                    label=label,
                    text=segment,
                    requires_user_input=label is RouteLabel.UNKNOWN,
                    metadata={"parser": "heuristic", "parent_route": RouteLabel.MIXED.value},
                )
            )

        return tuple(tasks)

    @staticmethod
    def _split_segments(text: str) -> list[str]:
        segments = []
        for raw_segment in _SPLIT_RE.split(text):
            segment = _LEADING_CONNECTOR_RE.sub("", raw_segment.strip())
            if segment:
                segments.append(segment)
        return segments

    @staticmethod
    def _infer_label(text: str) -> RouteLabel:
        normalized = normalize_text(text)
        if _FACTOR_PATTERN_RE.search(normalized):
            return RouteLabel.FACTOR_RESEARCH

        if any(keyword.lower() in normalized for keyword in DEFAULT_FACTOR_KEYWORDS):
            return RouteLabel.FACTOR_RESEARCH

        if any(keyword.lower() in normalized for keyword in DEFAULT_CHAT_KEYWORDS):
            return RouteLabel.CHAT

        if any(signal in normalized for signal in ("查", "看", "告诉", "是多少", "当前", "目前", "最新")):
            return RouteLabel.CHAT

        return RouteLabel.UNKNOWN
