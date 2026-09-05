import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from evoagent.core.context import ContextBuilder
from evoagent.core.events import InMemoryEventSink
from evoagent.core.loop import AgentLoop
from evoagent.core.models import (
    AgentLoopStatus,
    EventType,
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
from evoagent.providers.base import ProviderTimeoutError
from evoagent.providers.mock import MockProvider
from evoagent.tools.builtin.calculator import CalculatorTool
from evoagent.tools.executor import ToolExecutor
from evoagent.tools.registry import ToolRegistry


class BlockingProvider:
    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        await asyncio.sleep(60)
        if False:
            yield ProviderEvent(type=ProviderEventType.TEXT_DELTA, text_delta="unreachable")


def text_response(
    content: str,
    *,
    finish_reason: FinishReason = FinishReason.STOP,
    usage: TokenUsage | None = None,
) -> ModelResponse:
    return ModelResponse(
        message=Message(role=MessageRole.ASSISTANT, content=content),
        finish_reason=finish_reason,
        usage=usage,
    )


def tool_response(
    *calls: ToolCall, content: str | None = None, usage: TokenUsage | None = None
) -> ModelResponse:
    return ModelResponse(
        message=Message(role=MessageRole.ASSISTANT, content=content, tool_calls=calls),
        finish_reason=FinishReason.TOOL_CALLS,
        usage=usage,
    )


def make_loop(
    provider,
    *,
    max_iterations: int = 8,
    max_total_tokens: int = 32_000,
    max_repeated_tool_calls: int = 3,
) -> tuple[AgentLoop, InMemoryEventSink]:
    sink = InMemoryEventSink(uuid4())
    registry = ToolRegistry([CalculatorTool()])
    executor = ToolExecutor(
        registry,
        sink,
        timeout_seconds=1,
        max_result_chars=1_000,
    )
    loop = AgentLoop(
        provider,
        registry,
        executor,
        sink,
        model="mock-model",
        max_iterations=max_iterations,
        max_total_tokens=max_total_tokens,
        max_repeated_tool_calls=max_repeated_tool_calls,
    )
    return loop, sink


def initial_messages() -> tuple[Message, ...]:
    return ContextBuilder().build("calculate")


@pytest.mark.asyncio
async def test_loop_returns_direct_final_answer_and_usage() -> None:
    usage = TokenUsage(input_tokens=3, output_tokens=2, total_tokens=5)
    provider = MockProvider([text_response("done", usage=usage)])
    loop, sink = make_loop(provider)

    result = await loop.run(initial_messages())

    assert result.status is AgentLoopStatus.COMPLETED
    assert result.final_answer == "done"
    assert result.usage == usage
    assert result.iterations == 1
    assert [event.type for event in sink.events] == [
        EventType.MODEL_REQUESTED,
        EventType.MODEL_DELTA,
        EventType.MODEL_COMPLETED,
    ]


@pytest.mark.asyncio
async def test_loop_executes_tool_and_feeds_result_back_to_model() -> None:
    call = ToolCall(call_id="call-1", name="calculator", arguments={"expression": "12 * 3"})
    provider = MockProvider([tool_response(call), text_response("the answer is 36")])
    loop, _ = make_loop(provider)

    result = await loop.run(initial_messages())

    assert result.status is AgentLoopStatus.COMPLETED
    assert result.final_answer == "the answer is 36"
    assert len(provider.requests) == 2
    second_messages = provider.requests[1].messages
    assert second_messages[-2].role is MessageRole.ASSISTANT
    assert second_messages[-2].tool_calls == (call,)
    assert second_messages[-1].role is MessageRole.TOOL
    assert second_messages[-1].tool_call_id == "call-1"
    assert second_messages[-1].content == "36"


@pytest.mark.asyncio
async def test_loop_does_not_treat_text_with_tool_calls_as_final_answer() -> None:
    call = ToolCall(call_id="call-1", name="calculator", arguments={"expression": "2+2"})
    provider = MockProvider(
        [tool_response(call, content="I will calculate it"), text_response("4")]
    )
    loop, _ = make_loop(provider)

    result = await loop.run(initial_messages())

    assert result.final_answer == "4"
    assert result.iterations == 2
    assert len(provider.requests) == 2


@pytest.mark.asyncio
async def test_loop_feeds_unknown_tool_error_back_and_allows_recovery() -> None:
    call = ToolCall(call_id="call-1", name="missing")
    provider = MockProvider([tool_response(call), text_response("used another plan")])
    loop, _ = make_loop(provider)

    result = await loop.run(initial_messages())

    assert result.status is AgentLoopStatus.COMPLETED
    tool_message = provider.requests[1].messages[-1]
    assert tool_message.role is MessageRole.TOOL
    assert "tool_not_found" in (tool_message.content or "")


@pytest.mark.asyncio
async def test_loop_feeds_tool_execution_error_back_and_allows_recovery() -> None:
    call = ToolCall(call_id="call-1", name="calculator", arguments={"expression": "1 / 0"})
    provider = MockProvider([tool_response(call), text_response("cannot divide by zero")])
    loop, _ = make_loop(provider)

    result = await loop.run(initial_messages())

    assert result.status is AgentLoopStatus.COMPLETED
    assert "tool_execution_error" in (provider.requests[1].messages[-1].content or "")


@pytest.mark.asyncio
async def test_loop_rejects_non_normal_finish_without_tool_calls() -> None:
    provider = MockProvider([text_response("partial", finish_reason=FinishReason.LENGTH)])
    loop, _ = make_loop(provider)

    result = await loop.run(initial_messages())

    assert result.status is AgentLoopStatus.FAILED
    assert result.final_answer is None
    assert result.error_code == "model_finish_length"


@pytest.mark.asyncio
async def test_loop_reports_provider_error() -> None:
    provider = MockProvider([ProviderTimeoutError()])
    loop, sink = make_loop(provider)

    result = await loop.run(initial_messages())

    assert result.status is AgentLoopStatus.FAILED
    assert result.error_code == "provider_timeout"
    assert sink.events[-1].type is EventType.MODEL_FAILED


@pytest.mark.asyncio
async def test_loop_reports_incomplete_provider_stream() -> None:
    provider = MockProvider(
        [(ProviderEvent(type=ProviderEventType.TEXT_DELTA, text_delta="partial"),)]
    )
    loop, _ = make_loop(provider)

    result = await loop.run(initial_messages())

    assert result.status is AgentLoopStatus.FAILED
    assert result.error_code == "provider_protocol_error"


@pytest.mark.asyncio
async def test_loop_stops_after_maximum_iterations() -> None:
    calls = [
        ToolCall(call_id=f"call-{index}", name="calculator", arguments={"expression": "1+1"})
        for index in range(2)
    ]
    provider = MockProvider([tool_response(call) for call in calls])
    loop, _ = make_loop(provider, max_iterations=2)

    result = await loop.run(initial_messages())

    assert result.status is AgentLoopStatus.LIMIT_REACHED
    assert result.error_code == "max_iterations_reached"
    assert result.iterations == 2
    assert len(provider.requests) == 2


@pytest.mark.asyncio
async def test_loop_stops_before_next_request_when_token_budget_is_reached() -> None:
    usage = TokenUsage(input_tokens=3, output_tokens=2, total_tokens=5)
    call = ToolCall(call_id="call-1", name="calculator", arguments={"expression": "1+1"})
    provider = MockProvider([tool_response(call, usage=usage), text_response("unused")])
    loop, _ = make_loop(provider, max_total_tokens=5)

    result = await loop.run(initial_messages())

    assert result.status is AgentLoopStatus.LIMIT_REACHED
    assert result.error_code == "token_budget_reached"
    assert result.usage == usage
    assert len(provider.requests) == 1
    assert provider.remaining_steps == 1


@pytest.mark.asyncio
async def test_loop_stops_repeated_tool_calls_with_identical_results() -> None:
    responses = [
        tool_response(
            ToolCall(
                call_id=f"call-{index}",
                name="calculator",
                arguments={"expression": "1 + 1"},
            )
        )
        for index in range(4)
    ]
    provider = MockProvider(responses)
    loop, _ = make_loop(provider, max_repeated_tool_calls=3)

    result = await loop.run(initial_messages())

    assert result.status is AgentLoopStatus.LIMIT_REACHED
    assert result.error_code == "repeated_tool_calls"
    assert result.iterations == 3
    assert len(provider.requests) == 3
    assert provider.remaining_steps == 1


@pytest.mark.asyncio
async def test_loop_marks_usage_unknown_when_provider_omits_it() -> None:
    loop, _ = make_loop(MockProvider([text_response("done")]))

    result = await loop.run(initial_messages())

    assert result.status is AgentLoopStatus.COMPLETED
    assert result.usage is None


@pytest.mark.asyncio
async def test_loop_propagates_cancellation() -> None:
    loop, _ = make_loop(BlockingProvider())
    task = asyncio.create_task(loop.run(initial_messages()))
    await asyncio.sleep(0)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"model": " "}, "model"),
        ({"max_iterations": 0}, "max_iterations"),
        ({"max_total_tokens": 0}, "max_total_tokens"),
        ({"max_repeated_tool_calls": 0}, "max_repeated_tool_calls"),
    ],
)
def test_loop_rejects_invalid_settings(kwargs: dict[str, object], message: str) -> None:
    sink = InMemoryEventSink(uuid4())
    registry = ToolRegistry()
    executor = ToolExecutor(registry, sink, timeout_seconds=1, max_result_chars=10)
    settings = {
        "model": "mock-model",
        "max_iterations": 8,
        "max_total_tokens": 100,
        **kwargs,
    }

    with pytest.raises(ValueError, match=message):
        AgentLoop(MockProvider([]), registry, executor, sink, **settings)
