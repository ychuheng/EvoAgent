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


def test_sanitize_payload_redacts_secret_shaped_values() -> None:
    """§2.1 第 2 条：字符串**值**也过共享敏感原语。

    事件里最常见的形态是键名正常、值里带凭据（例如命令行参数、工具输出摘要），
    只做键名结构脱敏会漏掉它们。
    """

    sanitized = sanitize_payload(
        {
            "command": "psql postgres://appuser:s3cr3t-pw@db.internal/app",
            "token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW",
            "note": "password: hunter2 记在这里",
        },
        max_string_chars=500,
    )

    assert "s3cr3t-pw" not in str(sanitized)
    assert "dBjftJeZ4CVP" not in str(sanitized)
    assert "hunter2" not in str(sanitized)
    assert "[REDACTED]" in str(sanitized)


def test_sanitize_payload_leaves_normal_text_unchanged() -> None:
    """对照：正常内容不得被改写（新增检测不能变成"到处打码"）。"""

    payload = {
        "goal": "把 report.md 里的编号列改成字符串类型，并保留前导零",
        "url": "https://example.com/docs/user:pass@host 只是路径里的冒号",
        "version": "1.2.3",
    }

    assert sanitize_payload(payload, max_string_chars=500) == payload


def test_sanitize_payload_redacts_before_truncating() -> None:
    """先脱敏再截断：反过来会让跨越截断边界的秘密只被截掉一半。"""

    sanitized = sanitize_payload(
        {"content": "密码写在前面 password: hunter2 后面是很长的正文内容"},
        max_string_chars=12,
    )

    assert "hunter2" not in str(sanitized)
    assert sanitized["content"].endswith("chars]")
