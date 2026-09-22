"""Web-facing task handlers."""

from __future__ import annotations

from dataclasses import dataclass

from stock_common.agent.schemas import AgentStatus, TaskExecutionContext, TaskResult, TaskSpec
from stock_common.llm import (
    ChatLLMConfigError,
    ChatLLMError,
    ChatLLMSettings,
    ChatMessage,
    OpenAICompatibleChatClient,
)
from stock_common.router.schemas import RouteLabel
from stock_common.search import (
    SearchAnswerSynthesizer,
    SearchHistoryMessage,
    WebSearchClient,
    WebSearchConfigError,
    WebSearchError,
    WebSearchSettings,
)


@dataclass(frozen=True)
class RoutingPreviewHandler:
    """Return a concise response that reflects current non-chat routing behavior."""

    label: RouteLabel

    async def handle(self, task: TaskSpec, context: TaskExecutionContext) -> TaskResult:
        if task.label is RouteLabel.FACTOR_RESEARCH:
            content = "已识别为因子研究或回测任务。当前版本尚未接入真实回测工作流。"
            status = AgentStatus.COMPLETED
        elif task.label is RouteLabel.MIXED:
            content = "该请求包含多个意图，需要继续拆分后执行。"
            status = AgentStatus.NEEDS_USER_INPUT
        else:
            content = "暂时无法可靠判断任务类型，请补充更具体的金融问题或研究目标。"
            status = AgentStatus.NEEDS_USER_INPUT

        return TaskResult(
            task=task,
            content=content,
            status=status,
            model_selection=context.model_selection,
            metadata={
                "handler": "routing_preview",
                "route_label": task.label.value,
            },
        )


@dataclass(frozen=True)
class FinanceChatHandler:
    """Answer chat requests with the selected LLM, optionally with web search."""

    llm_client: OpenAICompatibleChatClient | None = None
    search_synthesizer: SearchAnswerSynthesizer | None = None

    async def handle(self, task: TaskSpec, context: TaskExecutionContext) -> TaskResult:
        if _metadata_bool(context.metadata.get("web_search")):
            return await self._handle_with_web_search(task, context)
        return await self._handle_with_llm(task, context)

    async def _handle_with_llm(
        self,
        task: TaskSpec,
        context: TaskExecutionContext,
    ) -> TaskResult:
        try:
            client = self.llm_client
            if client is None:
                client = OpenAICompatibleChatClient(
                    ChatLLMSettings.from_model_profile(context.model_selection.model)
                )
            content = await client.complete(_build_chat_messages(task, context))
        except ChatLLMConfigError as exc:
            return TaskResult(
                task=task,
                content=(
                    "Chat LLM is not configured. Set the selected model env values "
                    "or fallback env values such as DEEPSEEK_BASE_URL, "
                    "DEEPSEEK_API_KEY, and DEEPSEEK_MODEL."
                ),
                status=AgentStatus.NEEDS_USER_INPUT,
                model_selection=context.model_selection,
                metadata={
                    "handler": "llm_chat",
                    "web_search": False,
                    "error": "chat_llm_config_missing",
                    "detail": str(exc),
                },
            )
        except ChatLLMError as exc:
            return TaskResult(
                task=task,
                content=f"Chat LLM request failed: {exc}",
                status=AgentStatus.FAILED,
                model_selection=context.model_selection,
                metadata={
                    "handler": "llm_chat",
                    "web_search": False,
                    "error": "chat_llm_failed",
                },
            )

        return TaskResult(
            task=task,
            content=content,
            status=AgentStatus.COMPLETED,
            model_selection=context.model_selection,
            metadata={
                "handler": "llm_chat",
                "web_search": False,
            },
        )

    async def _handle_with_web_search(
        self,
        task: TaskSpec,
        context: TaskExecutionContext,
    ) -> TaskResult:
        try:
            synthesizer = self.search_synthesizer or SearchAnswerSynthesizer(
                client=WebSearchClient(WebSearchSettings.from_env())
            )
            answer = await synthesizer.answer(
                query=task.text,
                history=_build_search_history(context),
            )
        except WebSearchConfigError:
            return TaskResult(
                task=task,
                content=(
                    "需要先配置联网搜索 API 才能回答这个问题：请在 .env 中填写 "
                    "FINANCE_WEB_SEARCH_API_URL 和 FINANCE_WEB_SEARCH_API_KEY。"
                ),
                status=AgentStatus.NEEDS_USER_INPUT,
                model_selection=context.model_selection,
                metadata={
                    "handler": "search_grounded_chat",
                    "web_search": True,
                    "error": "web_search_config_missing",
                    "required_env": [
                        "FINANCE_WEB_SEARCH_API_URL",
                        "FINANCE_WEB_SEARCH_API_KEY",
                    ],
                },
            )
        except WebSearchError as exc:
            return TaskResult(
                task=task,
                content=f"联网搜索请求失败：{exc}",
                status=AgentStatus.FAILED,
                model_selection=context.model_selection,
                metadata={
                    "handler": "search_grounded_chat",
                    "web_search": True,
                    "error": "web_search_failed",
                },
            )

        return TaskResult(
            task=task,
            content=answer.content,
            status=AgentStatus.COMPLETED,
            model_selection=context.model_selection,
            metadata={
                "handler": "search_grounded_chat",
                "web_search": True,
                "search_query": answer.query,
                "search_results": [result.to_dict() for result in answer.results],
            },
        )


SearchGroundedChatHandler = FinanceChatHandler


def _build_chat_messages(
    task: TaskSpec,
    context: TaskExecutionContext,
) -> tuple[ChatMessage, ...]:
    messages = [
        ChatMessage(
            role="system",
            content=(
                "You are a finance-focused chat assistant. Answer in the user's "
                "language. If current market, filings, or news data is required "
                "and web search is not enabled, explain that live data is "
                "unavailable in this mode instead of inventing facts."
            ),
        )
    ]
    previous_messages = context.session.messages[:-1]

    for message in previous_messages[-8:]:
        role = message.role.value
        if role not in {"system", "user", "assistant"}:
            continue
        if role == "system" and len(messages) > 1:
            continue
        if messages and role == messages[-1].role and role != "system":
            continue
        messages.append(ChatMessage(role=role, content=message.content))

    messages.append(ChatMessage(role="user", content=task.text))
    return tuple(messages)


def _build_search_history(context: TaskExecutionContext) -> tuple[SearchHistoryMessage, ...]:
    history: list[SearchHistoryMessage] = []
    previous_messages = context.session.messages[:-1]

    for message in previous_messages[-8:]:
        role = message.role.value
        if role not in {"system", "user", "assistant"}:
            continue
        if role == "system" and history:
            continue
        if history and role == history[-1].role and role != "system":
            continue
        history.append(SearchHistoryMessage(role=role, content=message.content))

    return tuple(history)


def _metadata_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}
