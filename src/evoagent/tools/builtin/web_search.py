"""统一 Web Search 工具、确定性替身和 Brave 适配器。"""

import json
from collections.abc import Sequence
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from evoagent.core.models import ContractModel, ToolRisk
from evoagent.tools.base import BaseTool, ToolExecutionError


class SearchResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    title: str
    url: str
    snippet: str
    source: str


class SearchProvider(Protocol):
    async def search(self, query: str, *, count: int) -> tuple[SearchResult, ...]: ...


class MockSearchProvider:
    def __init__(self, results: Sequence[SearchResult]) -> None:
        self._results = tuple(results)

    async def search(self, query: str, *, count: int) -> tuple[SearchResult, ...]:
        return self._results[:count]


class BraveSearchProvider:
    """Brave Web Search API 的最小异步适配器。"""

    def __init__(self, api_key: SecretStr | str, *, timeout_seconds: float = 20) -> None:
        self._api_key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        self._timeout = timeout_seconds
        self._client = httpx.AsyncClient()

    async def search(self, query: str, *, count: int) -> tuple[SearchResult, ...]:
        try:
            response = await self._client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": count},
                headers={"X-Subscription-Token": self._api_key, "Accept": "application/json"},
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise ToolExecutionError("web search request failed") from error
        raw_results = payload.get("web", {}).get("results", [])
        return tuple(
            SearchResult(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                snippet=str(item.get("description", "")),
                source="brave",
            )
            for item in raw_results[:count]
            if isinstance(item, dict)
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class WebSearchArguments(ContractModel):
    query: str = Field(min_length=1, max_length=400)
    count: int = Field(default=5, ge=1, le=20)


class WebSearchTool(BaseTool[WebSearchArguments]):
    name = "web_search"
    description = "Search public web pages and return titles, URLs, and snippets."
    arguments_model = WebSearchArguments
    risk = ToolRisk.R0
    has_side_effects = False
    parallel_safe = True

    def __init__(self, provider: SearchProvider) -> None:
        self._provider = provider

    async def invoke(self, arguments: WebSearchArguments) -> str:
        results = await self._provider.search(arguments.query, count=arguments.count)
        return json.dumps(
            [result.model_dump(mode="json") for result in results], ensure_ascii=False
        )
