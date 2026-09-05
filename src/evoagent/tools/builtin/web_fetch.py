"""带最低 SSRF、重定向、超时和响应大小保护的网页工具。"""

from typing import Self
from urllib.parse import urljoin

import httpx
from pydantic import Field, field_validator

from evoagent.core.models import ContractModel, ToolRisk
from evoagent.tools.base import BaseTool, ToolExecutionError
from evoagent.tools.guards import URLGuard

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_TEXT_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
)


class WebFetchArguments(ContractModel):
    url: str = Field(min_length=1, max_length=8_192)

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        url = value.strip()
        if not url:
            raise ValueError("url cannot be blank")
        return url


class WebFetchTool(BaseTool[WebFetchArguments]):
    """逐跳校验 URL，并读取大小受限的文本响应。"""

    name = "web_fetch"
    description = "Fetch public HTTP or HTTPS text content with SSRF protections."
    arguments_model = WebFetchArguments
    risk = ToolRisk.R0
    has_side_effects = False
    parallel_safe = True

    def __init__(
        self,
        url_guard: URLGuard,
        *,
        timeout_seconds: float,
        max_response_bytes: int = 1_000_000,
        max_redirects: int = 5,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        if max_redirects < 0:
            raise ValueError("max_redirects cannot be negative")
        self._url_guard = url_guard
        self._timeout = httpx.Timeout(timeout_seconds)
        self._max_response_bytes = max_response_bytes
        self._max_redirects = max_redirects
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def invoke(self, arguments: WebFetchArguments) -> str:
        current_url = arguments.url
        for redirect_count in range(self._max_redirects + 1):
            safe_url = await self._url_guard.validate(current_url)
            try:
                async with self._client.stream(
                    "GET",
                    safe_url,
                    headers={
                        "Accept": "text/*, application/json, application/xml",
                        "User-Agent": "EvoAgent/0.1",
                    },
                    follow_redirects=False,
                    timeout=self._timeout,
                ) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        if redirect_count >= self._max_redirects:
                            raise ToolExecutionError("web redirect limit exceeded")
                        location = response.headers.get("location")
                        if not location:
                            raise ToolExecutionError("redirect response has no Location header")
                        current_url = urljoin(safe_url, location)
                        continue
                    if response.status_code < 200 or response.status_code >= 300:
                        raise ToolExecutionError(f"web server returned HTTP {response.status_code}")

                    content_type = response.headers.get("content-type", "").lower()
                    media_type = content_type.split(";", 1)[0].strip()
                    if not any(media_type.startswith(allowed) for allowed in _TEXT_CONTENT_TYPES):
                        raise ToolExecutionError(
                            f"web response is not a supported text type: {media_type or 'unknown'}"
                        )
                    raw_length = response.headers.get("content-length")
                    if raw_length is not None:
                        try:
                            content_length = int(raw_length)
                        except ValueError as error:
                            raise ToolExecutionError("invalid Content-Length header") from error
                        if content_length < 0:
                            raise ToolExecutionError("invalid Content-Length header")
                        if content_length > self._max_response_bytes:
                            raise ToolExecutionError("web response exceeds the size limit")

                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > self._max_response_bytes:
                            raise ToolExecutionError("web response exceeds the size limit")
                    encoding = response.encoding or "utf-8"
                    try:
                        return bytes(content).decode(encoding, errors="replace")
                    except LookupError as error:
                        raise ToolExecutionError("web response uses an unknown encoding") from error
            except httpx.TimeoutException as error:
                raise ToolExecutionError("web request timed out") from error
            except httpx.RequestError as error:
                raise ToolExecutionError(f"web request failed: {type(error).__name__}") from error

        raise ToolExecutionError("web redirect limit exceeded")
