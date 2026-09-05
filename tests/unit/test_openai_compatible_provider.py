import json

import httpx
import pytest
import respx

from evoagent.core.models import (
    FinishReason,
    Message,
    MessageRole,
    ModelRequest,
    ProviderEventType,
    ToolDefinition,
)
from evoagent.providers.base import ProviderError, ProviderProtocolError, ProviderTimeoutError
from evoagent.providers.openai_compatible import OpenAICompatibleProvider

ENDPOINT = "https://llm.example.test/v1/chat/completions"


def sse(*items: object, done: bool = True) -> str:
    lines = [f"data: {json.dumps(item)}\n\n" for item in items]
    if done:
        lines.append("data: [DONE]\n\n")
    return "".join(lines)


def make_request(*, with_tool: bool = False) -> ModelRequest:
    tools = ()
    if with_tool:
        tools = (
            ToolDefinition(
                name="calculator",
                description="Calculate an expression.",
                parameters={"type": "object"},
            ),
        )
    return ModelRequest(
        messages=(Message(role=MessageRole.USER, content="hello"),),
        tool_definitions=tools,
        model="test-model",
    )


@pytest.mark.asyncio
@respx.mock
async def test_provider_streams_text_usage_and_completed_response() -> None:
    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            text=sse(
                {"choices": [{"index": 0, "delta": {"content": "hel"}}]},
                {"choices": [{"index": 0, "delta": {"content": "lo"}, "finish_reason": "stop"}]},
                {
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 1,
                        "total_tokens": 3,
                    },
                },
            ),
            headers={"content-type": "text/event-stream"},
        )
    )
    async with OpenAICompatibleProvider(
        api_key="test-key", base_url="https://llm.example.test/v1", timeout_seconds=1
    ) as provider:
        events = [event async for event in provider.stream(make_request())]

    assert [event.type for event in events] == [
        ProviderEventType.TEXT_DELTA,
        ProviderEventType.TEXT_DELTA,
        ProviderEventType.USAGE,
        ProviderEventType.COMPLETED,
    ]
    assert events[-1].response is not None
    assert events[-1].response.message.content == "hello"
    assert events[-1].response.finish_reason is FinishReason.STOP
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer test-key"
    assert json.loads(request.content)["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
@respx.mock
async def test_provider_assembles_fragmented_tool_call_arguments() -> None:
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            text=sse(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-1",
                                        "function": {
                                            "name": "calculator",
                                            "arguments": '{"expression":',
                                        },
                                    }
                                ]
                            },
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [{"index": 0, "function": {"arguments": '"1 + 1"}'}}]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
            ),
        )
    )
    async with OpenAICompatibleProvider(
        api_key="test-key", base_url="https://llm.example.test/v1", timeout_seconds=1
    ) as provider:
        events = [event async for event in provider.stream(make_request(with_tool=True))]

    completed = events[-1].response
    assert completed is not None
    assert completed.message.tool_calls[0].call_id == "call-1"
    assert completed.message.tool_calls[0].arguments == {"expression": "1 + 1"}
    assert completed.finish_reason is FinishReason.TOOL_CALLS


@pytest.mark.asyncio
@respx.mock
async def test_provider_assembles_interleaved_tool_calls_in_index_order() -> None:
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            text=sse(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 1,
                                        "id": "call-2",
                                        "function": {
                                            "name": "file_",
                                            "arguments": '{"path":',
                                        },
                                    },
                                    {
                                        "index": 0,
                                        "id": "call-1",
                                        "function": {
                                            "name": "calcu",
                                            "arguments": '{"expression":',
                                        },
                                    },
                                ]
                            },
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {
                                            "name": "lator",
                                            "arguments": '"2 + 3"}',
                                        },
                                    },
                                    {
                                        "index": 1,
                                        "function": {
                                            "name": "read",
                                            "arguments": '"notes.txt"}',
                                        },
                                    },
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
            ),
        )
    )
    provider = OpenAICompatibleProvider(
        api_key="test-key", base_url="https://llm.example.test/v1", timeout_seconds=1
    )

    events = [event async for event in provider.stream(make_request(with_tool=True))]
    await provider.aclose()

    completed = events[-1].response
    assert completed is not None
    assert [call.name for call in completed.message.tool_calls] == ["calculator", "file_read"]
    assert [call.arguments for call in completed.message.tool_calls] == [
        {"expression": "2 + 3"},
        {"path": "notes.txt"},
    ]


@pytest.mark.asyncio
@respx.mock
async def test_provider_classifies_http_errors() -> None:
    respx.post(ENDPOINT).mock(return_value=httpx.Response(429, text="rate limited"))
    provider = OpenAICompatibleProvider(
        api_key="test-key", base_url="https://llm.example.test/v1", timeout_seconds=1
    )

    with pytest.raises(ProviderError) as captured:
        [event async for event in provider.stream(make_request())]
    await provider.aclose()

    assert captured.value.code == "provider_rate_limit"
    assert "429" in str(captured.value)


@pytest.mark.asyncio
@respx.mock
async def test_provider_converts_http_timeout() -> None:
    respx.post(ENDPOINT).mock(side_effect=httpx.ReadTimeout("slow"))
    provider = OpenAICompatibleProvider(
        api_key="test-key", base_url="https://llm.example.test/v1", timeout_seconds=1
    )

    with pytest.raises(ProviderTimeoutError):
        [event async for event in provider.stream(make_request())]
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_provider_rejects_stream_without_done_marker() -> None:
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            text=sse(
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                done=False,
            ),
        )
    )
    provider = OpenAICompatibleProvider(
        api_key="test-key", base_url="https://llm.example.test/v1", timeout_seconds=1
    )

    with pytest.raises(ProviderProtocolError, match="DONE"):
        [event async for event in provider.stream(make_request())]
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_provider_rejects_invalid_tool_arguments_json() -> None:
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            text=sse(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-1",
                                        "function": {
                                            "name": "calculator",
                                            "arguments": "{bad-json",
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            ),
        )
    )
    provider = OpenAICompatibleProvider(
        api_key="test-key", base_url="https://llm.example.test/v1", timeout_seconds=1
    )

    with pytest.raises(ProviderProtocolError, match="arguments"):
        [event async for event in provider.stream(make_request(with_tool=True))]
    await provider.aclose()
