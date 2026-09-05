import pytest

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
from evoagent.providers.base import ProviderTimeoutError
from evoagent.providers.mock import MockProvider, MockProviderExhaustedError


def make_request() -> ModelRequest:
    return ModelRequest(
        messages=(Message(role=MessageRole.USER, content="hello"),),
        model="mock-model",
    )


@pytest.mark.asyncio
async def test_mock_provider_expands_text_response_and_records_request() -> None:
    usage = TokenUsage(input_tokens=2, output_tokens=3, total_tokens=5)
    response = ModelResponse(
        message=Message(role=MessageRole.ASSISTANT, content="hello back"),
        finish_reason=FinishReason.STOP,
        usage=usage,
    )
    provider = MockProvider([response])
    request = make_request()

    events = [event async for event in provider.stream(request)]

    assert [event.type for event in events] == [
        ProviderEventType.TEXT_DELTA,
        ProviderEventType.USAGE,
        ProviderEventType.COMPLETED,
    ]
    assert events[0].text_delta == "hello back"
    assert events[1].usage == usage
    assert events[2].response == response
    assert provider.requests == (request,)
    assert provider.remaining_steps == 0


@pytest.mark.asyncio
async def test_mock_provider_expands_tool_calls() -> None:
    call = ToolCall(call_id="call-1", name="calculator", arguments={"expression": "1 + 1"})
    response = ModelResponse(
        message=Message(role=MessageRole.ASSISTANT, tool_calls=(call,)),
        finish_reason=FinishReason.TOOL_CALLS,
    )
    provider = MockProvider([response])

    events = [event async for event in provider.stream(make_request())]

    assert [event.type for event in events] == [
        ProviderEventType.TOOL_CALL_DELTA,
        ProviderEventType.COMPLETED,
    ]
    assert events[0].tool_call_id == "call-1"
    assert events[0].tool_name_delta == "calculator"
    assert events[0].arguments_delta == '{"expression":"1 + 1"}'


@pytest.mark.asyncio
async def test_mock_provider_can_replay_an_explicit_event_sequence() -> None:
    explicit = (
        ProviderEvent(type=ProviderEventType.TEXT_DELTA, text_delta="part-1"),
        ProviderEvent(type=ProviderEventType.TEXT_DELTA, text_delta="part-2"),
    )
    provider = MockProvider([explicit])

    events = [event async for event in provider.stream(make_request())]

    assert events == list(explicit)


@pytest.mark.asyncio
async def test_mock_provider_raises_scripted_provider_error() -> None:
    provider = MockProvider([ProviderTimeoutError()])

    with pytest.raises(ProviderTimeoutError):
        [event async for event in provider.stream(make_request())]


@pytest.mark.asyncio
async def test_mock_provider_reports_exhausted_script() -> None:
    provider = MockProvider([])

    with pytest.raises(MockProviderExhaustedError) as captured:
        [event async for event in provider.stream(make_request())]

    assert captured.value.code == "mock_script_exhausted"
    assert len(provider.requests) == 1
