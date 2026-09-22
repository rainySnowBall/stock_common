"""Synthesize concise answers from web-search results."""

from __future__ import annotations

from dataclasses import dataclass

from stock_common.search.schemas import SearchAnswer, SearchHistoryMessage, SearchResult
from stock_common.search.web_search import WebSearchClient


@dataclass(frozen=True)
class SearchAnswerSynthesizer:
    """Search the web and summarize multiple results into one answer."""

    client: WebSearchClient
    max_result_chars: int = 360

    async def answer(
        self,
        query: str,
        history: tuple[SearchHistoryMessage, ...] = (),
    ) -> SearchAnswer:
        results = await self.client.search(query=query, history=history)
        content = self.synthesize(query=query, results=results)
        return SearchAnswer(query=query, content=content, results=results)

    def synthesize(self, query: str, results: tuple[SearchResult, ...]) -> str:
        if not results:
            return (
                "我没有从联网搜索中拿到可用结果。可以换一个更具体的问题，"
                "或检查搜索 API 的配置与返回格式。"
            )

        lines = [f"根据联网搜索结果，关于“{query}”可以先这样看："]
        for index, result in enumerate(results, start=1):
            text = self._clean_text(result.best_text)
            if len(text) > self.max_result_chars:
                text = text[: self.max_result_chars - 1].rstrip() + "…"

            source = result.source or result.title
            if result.url:
                lines.append(f"{index}. {source}：{text}（来源：{result.url}）")
            else:
                lines.append(f"{index}. {source}：{text}")

        lines.append("综合来看，以上结果可以作为当前回答依据；涉及实时行情、财报或新闻时，建议以来源页面的最新发布时间为准。")
        return "\n".join(lines)

    @staticmethod
    def _clean_text(text: str) -> str:
        return " ".join(text.split())
