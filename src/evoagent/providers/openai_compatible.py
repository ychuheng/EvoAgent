"""OpenAI-compatible Chat Completions 流式模型适配器。"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Self

import httpx
from pydantic import SecretStr, ValidationError

from evoagent.core.models import (
    FinishReason,
    Message,
    MessageRole,
    ModelRequest,
    ModelResponse,
    ProviderEvent,
    ProviderEventType,
    TokenUsage,
    ToolCall,
)
from evoagent.providers.base import ProviderError, ProviderProtocolError, ProviderTimeoutError


@dataclass
class _ToolCallBuffer:
    """临时保存同一个流式 ToolCall 的分片。"""

    call_id: str = ""
    name: str = ""
    arguments: str = ""


class OpenAICompatibleProvider:
    """适配 `/v1/chat/completions` 的 Function Calling 与 SSE 子集。"""

    def __init__(
        self,
        *,
        api_key: SecretStr | str,
        base_url: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        secret = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        if not secret.strip():
            raise ValueError("api_key cannot be blank")
        normalized_base = base_url.strip().rstrip("/")
        if not normalized_base:
            raise ValueError("base_url cannot be blank")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        self._api_key = secret
        self._endpoint = (
            normalized_base
            if normalized_base.endswith("/chat/completions")
            else f"{normalized_base}/chat/completions"
        )
        self._timeout = httpx.Timeout(timeout_seconds)
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """只关闭由当前 Provider 自己创建的 HTTP 客户端。"""

        if self._owns_client:
            await self._client.aclose()

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        """发送一次流式请求，并产生统一 ProviderEvent。"""

        payload = self._build_payload(request)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }

        try:
            async with self._client.stream(
                "POST",
                self._endpoint,
                headers=headers,
                json=payload,
                timeout=self._timeout,
            ) as response:
                if response.status_code >= 400:
                    await self._raise_http_error(response)

                content_parts: list[str] = []
                tool_buffers: dict[int, _ToolCallBuffer] = {}
                finish_reason: FinishReason | None = None
                usage: TokenUsage | None = None
                received_done = False

                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        raise ProviderProtocolError("SSE line does not start with data:")
                    data = line[5:].lstrip()
                    if data == "[DONE]":
                        received_done = True
                        break

                    chunk = self._parse_chunk(data)
                    if "error" in chunk:
                        error = chunk["error"]
                        message = (
                            str(error.get("message", "provider returned an API error"))
                            if isinstance(error, dict)
                            else str(error)
                        )
                        raise ProviderError(message[:500], code="provider_api_error")

                    raw_usage = chunk.get("usage")
                    if raw_usage is not None:
                        if usage is not None:
                            raise ProviderProtocolError("provider emitted usage more than once")
                        usage = self._parse_usage(raw_usage)
                        yield ProviderEvent(type=ProviderEventType.USAGE, usage=usage)

                    choices = chunk.get("choices", [])
                    if not isinstance(choices, list):
                        raise ProviderProtocolError("choices must be a list")
                    if len(choices) > 1:
                        raise ProviderProtocolError("only one streamed choice is supported")
                    if not choices:
                        continue

                    choice = choices[0]
                    if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                        raise ProviderProtocolError("streamed choice must have index 0")
                    delta = choice.get("delta", {})
                    if not isinstance(delta, dict):
                        raise ProviderProtocolError("choice delta must be an object")

                    content = delta.get("content")
                    if content is not None:
                        if not isinstance(content, str):
                            raise ProviderProtocolError("content delta must be a string")
                        content_parts.append(content)
                        yield ProviderEvent(
                            type=ProviderEventType.TEXT_DELTA,
                            text_delta=content,
                        )

                    async for event in self._consume_tool_deltas(delta, tool_buffers):
                        yield event

                    raw_finish_reason = choice.get("finish_reason")
                    if raw_finish_reason is not None:
                        parsed_finish = self._parse_finish_reason(raw_finish_reason)
                        if finish_reason is not None and finish_reason is not parsed_finish:
                            raise ProviderProtocolError("finish_reason changed during the stream")
                        finish_reason = parsed_finish

        except httpx.TimeoutException as error:
            raise ProviderTimeoutError() from error
        except httpx.RequestError as error:
            raise ProviderError(
                f"model request failed: {type(error).__name__}",
                code="provider_network_error",
            ) from error

        if not received_done:
            raise ProviderProtocolError("provider stream ended without [DONE]")
        if finish_reason is None:
            raise ProviderProtocolError("provider stream did not include finish_reason")

        try:
            tool_calls = self._build_tool_calls(tool_buffers)
            message = Message(
                role=MessageRole.ASSISTANT,
                content="".join(content_parts) if content_parts else None,
                tool_calls=tool_calls,
            )
            completed = ModelResponse(
                message=message,
                finish_reason=finish_reason,
                usage=usage,
            )
        except (ValidationError, ValueError) as error:
            raise ProviderProtocolError(f"invalid completed response: {error}") from error
        yield ProviderEvent(type=ProviderEventType.COMPLETED, response=completed)

    @staticmethod
    def _build_payload(request: ModelRequest) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        for message in request.messages:
            item: dict[str, Any] = {"role": message.role.value, "content": message.content}
            if message.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(
                                call.arguments,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        },
                    }
                    for call in message.tool_calls
                ]
            if message.role is MessageRole.TOOL:
                item["tool_call_id"] = message.tool_call_id
            messages.append(item)

        payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.tool_definitions:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": definition.name,
                        "description": definition.description,
                        "parameters": definition.parameters,
                    },
                }
                for definition in request.tool_definitions
            ]
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens
        return payload

    @staticmethod
    def _parse_chunk(data: str) -> dict[str, Any]:
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError as error:
            raise ProviderProtocolError("SSE data is not valid JSON") from error
        if not isinstance(chunk, dict):
            raise ProviderProtocolError("SSE data root must be an object")
        return chunk

    @staticmethod
    def _parse_usage(raw_usage: object) -> TokenUsage:
        if not isinstance(raw_usage, dict):
            raise ProviderProtocolError("usage must be an object")
        try:
            return TokenUsage(
                input_tokens=raw_usage["prompt_tokens"],
                output_tokens=raw_usage["completion_tokens"],
                total_tokens=raw_usage["total_tokens"],
            )
        except (KeyError, TypeError, ValidationError) as error:
            raise ProviderProtocolError("usage fields are invalid") from error

    @staticmethod
    async def _consume_tool_deltas(
        delta: dict[str, Any], buffers: dict[int, _ToolCallBuffer]
    ) -> AsyncIterator[ProviderEvent]:
        raw_tool_calls = delta.get("tool_calls", [])
        if not isinstance(raw_tool_calls, list):
            raise ProviderProtocolError("tool_calls delta must be a list")

        for raw_call in raw_tool_calls:
            if not isinstance(raw_call, dict):
                raise ProviderProtocolError("tool call delta must be an object")
            index = raw_call.get("index")
            if not isinstance(index, int) or isinstance(index, bool) or index < 0:
                raise ProviderProtocolError("tool call delta requires a non-negative index")
            buffer = buffers.setdefault(index, _ToolCallBuffer())

            call_id = raw_call.get("id")
            if call_id is not None:
                if not isinstance(call_id, str):
                    raise ProviderProtocolError("tool call id delta must be a string")
                if buffer.call_id and buffer.call_id != call_id:
                    raise ProviderProtocolError("tool call id changed during the stream")
                buffer.call_id = call_id

            function = raw_call.get("function", {})
            if not isinstance(function, dict):
                raise ProviderProtocolError("tool call function delta must be an object")
            name_delta = function.get("name")
            arguments_delta = function.get("arguments")
            if name_delta is not None:
                if not isinstance(name_delta, str):
                    raise ProviderProtocolError("tool name delta must be a string")
                buffer.name += name_delta
            if arguments_delta is not None:
                if not isinstance(arguments_delta, str):
                    raise ProviderProtocolError("tool arguments delta must be a string")
                buffer.arguments += arguments_delta

            if call_id is not None or name_delta is not None or arguments_delta is not None:
                yield ProviderEvent(
                    type=ProviderEventType.TOOL_CALL_DELTA,
                    tool_call_index=index,
                    tool_call_id=call_id,
                    tool_name_delta=name_delta,
                    arguments_delta=arguments_delta,
                )

    @staticmethod
    def _parse_finish_reason(raw_reason: object) -> FinishReason:
        if not isinstance(raw_reason, str):
            raise ProviderProtocolError("finish_reason must be a string")
        try:
            return FinishReason(raw_reason)
        except ValueError as error:
            raise ProviderProtocolError(f"unsupported finish_reason: {raw_reason}") from error

    @staticmethod
    def _build_tool_calls(buffers: dict[int, _ToolCallBuffer]) -> tuple[ToolCall, ...]:
        if not buffers:
            return ()
        indexes = sorted(buffers)
        if indexes != list(range(len(indexes))):
            raise ProviderProtocolError("tool call indexes must be contiguous from zero")

        calls: list[ToolCall] = []
        for index in indexes:
            buffer = buffers[index]
            if not buffer.call_id or not buffer.name:
                raise ProviderProtocolError("tool call is missing id or function name")
            try:
                arguments = json.loads(buffer.arguments or "{}")
            except json.JSONDecodeError as error:
                raise ProviderProtocolError("tool call arguments are not valid JSON") from error
            if not isinstance(arguments, dict):
                raise ProviderProtocolError("tool call arguments must be a JSON object")
            calls.append(ToolCall(call_id=buffer.call_id, name=buffer.name, arguments=arguments))
        return tuple(calls)

    @staticmethod
    async def _raise_http_error(response: httpx.Response) -> None:
        body = (await response.aread()).decode("utf-8", errors="replace")[:500]
        if response.status_code in {401, 403}:
            code = "provider_auth_error"
        elif response.status_code == 429:
            code = "provider_rate_limit"
        elif response.status_code >= 500:
            code = "provider_server_error"
        else:
            code = "provider_http_error"
        raise ProviderError(
            f"model service returned HTTP {response.status_code}: {body}",
            code=code,
        )
