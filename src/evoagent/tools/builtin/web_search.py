"""统一 Web Search 工具、确定性替身和 Brave 适配器。"""

import json
from collections.abc import Sequence
from typing import Protocol
from urllib.parse import urlsplit

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
    name = "mock"

    def __init__(self, results: Sequence[SearchResult]) -> None:
        self._results = tuple(results)

    async def search(self, query: str, *, count: int) -> tuple[SearchResult, ...]:
        return self._results[:count]


class BraveSearchProvider:
    """Brave Web Search API 的最小异步适配器。"""

    name = "brave"

    def __init__(
        self,
        api_key: SecretStr | str,
        *,
        timeout_seconds: float = 20,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        self._timeout = timeout_seconds
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None

    async def search(self, query: str, *, count: int) -> tuple[SearchResult, ...]:
        try:
            response = await self._client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": count},
                headers={"X-Subscription-Token": self._api_key, "Accept": "application/json"},
                timeout=self._timeout,
            )
        except httpx.TimeoutException as error:
            raise WebSearchError("search_timeout", "web search timed out") from error
        except httpx.RequestError as error:
            raise WebSearchError("search_network_error", "web search connection failed") from error
        if response.status_code >= 400:
            code = (
                "search_auth_failed"
                if response.status_code == 401
                else "search_forbidden"
                if response.status_code == 403
                else "search_rate_limited"
                if response.status_code == 429
                else "search_service_unavailable"
                if response.status_code >= 500
                else "search_http_error"
            )
            raise WebSearchError(code, f"web search returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as error:
            raise WebSearchError(
                "search_invalid_response", "web search returned invalid JSON"
            ) from error
        if not isinstance(payload, dict) or not isinstance(payload.get("web"), dict):
            raise WebSearchError(
                "search_invalid_response", "web search response has no web results"
            )
        raw_results = payload["web"].get("results", [])
        if not isinstance(raw_results, list):
            raise WebSearchError("search_invalid_response", "web search results must be a list")
        results: list[SearchResult] = []
        for item in raw_results[:count]:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                raise WebSearchError("search_invalid_response", "web search result has no URL")
            try:
                url = httpx.URL(item["url"])
                parsed_url = urlsplit(item["url"])
            except (httpx.InvalidURL, ValueError) as error:
                raise WebSearchError(
                    "search_invalid_response", "web search result URL is invalid"
                ) from error
            if (
                url.scheme not in {"http", "https"}
                or not url.host
                or parsed_url.username is not None
                or parsed_url.password is not None
            ):
                raise WebSearchError("search_invalid_response", "web search result URL is invalid")
            results.append(
                SearchResult(
                    title=str(item.get("title", "")),
                    url=item["url"],
                    snippet=str(item.get("description", "")),
                    source="brave",
                )
            )
        return tuple(results)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class WebSearchError(ToolExecutionError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


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
        content, _evidence = await self.invoke_with_evidence(arguments)
        return content

    async def invoke_with_evidence(
        self, arguments: WebSearchArguments
    ) -> tuple[str, dict[str, object]]:
        results = await self._provider.search(arguments.query, count=arguments.count)
        entries = [result.model_dump(mode="json") for result in results]
        return json.dumps(entries, ensure_ascii=False), {
            "level": "search_snippet",
            "query": arguments.query,
            "provider": getattr(self._provider, "name", "unknown"),
            "results": entries,
        }
