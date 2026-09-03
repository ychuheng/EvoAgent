import asyncio
from uuid import uuid4

import pytest

from evoagent.core.events import REDACTED, InMemoryEventSink, sanitize_payload
from evoagent.core.models import EventType


@pytest.mark.asyncio
async def test_event_sink_assigns_contiguous_sequences() -> None:
    run_id = uuid4()
    sink = InMemoryEventSink(run_id)

    await sink.emit(EventType.RUN_STARTED, {"goal": "calculate"})
    await sink.emit(EventType.MODEL_REQUESTED, {"iteration": 1})

    assert [event.sequence for event in sink.events] == [1, 2]
    assert all(event.run_id == run_id for event in sink.events)


@pytest.mark.asyncio
async def test_event_sink_sequence_is_safe_under_concurrency() -> None:
    sink = InMemoryEventSink(uuid4())

    await asyncio.gather(
        *(sink.emit(EventType.MODEL_DELTA, {"index": index}) for index in range(25))
    )

    assert [event.sequence for event in sink.events] == list(range(1, 26))


@pytest.mark.asyncio
async def test_event_sink_redacts_nested_secrets_and_reasoning() -> None:
    sink = InMemoryEventSink(uuid4())

    event = await sink.emit(
        EventType.MODEL_REQUESTED,
        {
            "headers": {"Authorization": "Bearer secret"},
            "api-key": "secret",
            "reasoning_content": "hidden reasoning",
            "input_tokens": 12,
        },
    )

    assert event.payload["headers"] == {"Authorization": REDACTED}
    assert event.payload["api-key"] == REDACTED
    assert event.payload["reasoning_content"] == REDACTED
    assert event.payload["input_tokens"] == 12


def test_sanitize_payload_truncates_large_strings() -> None:
    sanitized = sanitize_payload({"content": "abcdef"}, max_string_chars=3)

    assert sanitized == {"content": "abc…[truncated 3 chars]"}


def test_sanitize_payload_rejects_non_json_values() -> None:
    with pytest.raises(TypeError, match="not JSON-compatible"):
        sanitize_payload({"value": object()}, max_string_chars=10)

    with pytest.raises(TypeError, match="finite"):
        sanitize_payload({"value": float("nan")}, max_string_chars=10)
