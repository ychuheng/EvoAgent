from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from evoagent.core.models import (
    EventType,
    FinishReason,
    Message,
    MessageRole,
    ModelResponse,
    ProviderEvent,
    ProviderEventType,
    RunResult,
    RunStatus,
    RuntimeEvent,
    TokenUsage,
    ToolCall,
    ToolResult,
    ToolResultStatus,
)


def test_assistant_tool_call_and_tool_result_keep_correlation_id() -> None:
    call = ToolCall(call_id="call-1", name="calculator", arguments={"expression": "1+1"})
    assistant = Message(role=MessageRole.ASSISTANT, tool_calls=(call,))
    result = ToolResult(
        tool_call_id=call.call_id,
        name=call.name,
        status=ToolResultStatus.SUCCESS,
        content="2",
    )
    tool_message = Message(
        role=MessageRole.TOOL,
        content=result.content,
        tool_call_id=result.tool_call_id,
    )

    assert assistant.tool_calls[0].call_id == tool_message.tool_call_id


@pytest.mark.parametrize(
    "message",
    [
        {"role": MessageRole.USER, "content": "   "},
        {"role": MessageRole.TOOL, "content": "2"},
        {"role": MessageRole.SYSTEM, "content": "rules", "tool_call_id": "call-1"},
        {"role": MessageRole.ASSISTANT},
    ],
)
def test_invalid_message_shapes_are_rejected(message: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Message.model_validate(message)


def test_failed_tool_result_requires_error_code() -> None:
    with pytest.raises(ValidationError, match="error_code"):
        ToolResult(
            tool_call_id="call-1",
            name="calculator",
            status=ToolResultStatus.ERROR,
            content="division by zero",
        )


def test_token_usage_total_must_match_parts() -> None:
    with pytest.raises(ValidationError, match="total_tokens"):
        TokenUsage(input_tokens=10, output_tokens=5, total_tokens=99)


def test_token_usage_can_be_added() -> None:
    first = TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)
    second = TokenUsage(input_tokens=4, output_tokens=1, total_tokens=5)

    assert first + second == TokenUsage(input_tokens=14, output_tokens=6, total_tokens=20)


def test_model_response_with_tool_calls_requires_matching_finish_reason() -> None:
    message = Message(
        role=MessageRole.ASSISTANT,
        tool_calls=(ToolCall(call_id="call-1", name="calculator", arguments={}),),
    )

    with pytest.raises(ValidationError, match="finish_reason=tool_calls"):
        ModelResponse(message=message, finish_reason=FinishReason.STOP)


def test_provider_event_requires_payload_for_its_type() -> None:
    with pytest.raises(ValidationError, match="text_delta"):
        ProviderEvent(type=ProviderEventType.TEXT_DELTA)

    event = ProviderEvent(type=ProviderEventType.TEXT_DELTA, text_delta="hello")
    assert event.text_delta == "hello"

    with pytest.raises(ValidationError, match="another event type"):
        ProviderEvent(
            type=ProviderEventType.TEXT_DELTA,
            text_delta="hello",
            tool_call_index=0,
        )


def test_runtime_event_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        RuntimeEvent(
            run_id=uuid4(),
            sequence=1,
            type=EventType.RUN_STARTED,
            timestamp=datetime.now(),
        )


def test_run_result_requires_contiguous_events_from_same_run() -> None:
    run_id = uuid4()
    event = RuntimeEvent(
        run_id=run_id,
        sequence=2,
        type=EventType.RUN_COMPLETED,
        timestamp=datetime.now(UTC),
    )

    with pytest.raises(ValidationError, match="contiguous"):
        RunResult(
            run_id=run_id,
            status=RunStatus.COMPLETED,
            final_answer="done",
            usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            events=(event,),
        )


def test_completed_run_requires_answer_and_forbids_error() -> None:
    run_id = uuid4()
    event = RuntimeEvent(
        run_id=run_id,
        sequence=1,
        type=EventType.RUN_COMPLETED,
        timestamp=datetime.now(UTC),
    )

    with pytest.raises(ValidationError, match="final_answer"):
        RunResult(
            run_id=run_id,
            status=RunStatus.COMPLETED,
            usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            events=(event,),
        )
