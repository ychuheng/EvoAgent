"""支持顺序编号和载荷脱敏的运行事件收集功能。"""

import asyncio
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import JsonValue

from evoagent.core.models import EventType, RuntimeEvent

REDACTED = "[REDACTED]"
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "chain_of_thought",
    "reasoning_content",
    "thinking",
)


class RuntimeEventSink(Protocol):
    """接收同一次 Run 有序事件的目标接口。"""

    @property
    def events(self) -> tuple[RuntimeEvent, ...]: ...

    async def emit(
        self, event_type: EventType, payload: Mapping[str, Any] | None = None
    ) -> RuntimeEvent: ...


def sanitize_payload(value: Any, *, max_string_chars: int) -> JsonValue:
    """移除敏感信息并截断过长字符串，返回与 JSON 兼容的值。"""

    if isinstance(value, Mapping):
        sanitized: dict[str, JsonValue] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized_key = key.lower().replace("-", "_")
            if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
                sanitized[key] = REDACTED
            else:
                sanitized[key] = sanitize_payload(item, max_string_chars=max_string_chars)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [sanitize_payload(item, max_string_chars=max_string_chars) for item in value]
    if isinstance(value, str):
        if len(value) <= max_string_chars:
            return value
        omitted = len(value) - max_string_chars
        return f"{value[:max_string_chars]}…[truncated {omitted} chars]"
    if isinstance(value, float) and not math.isfinite(value):
        raise TypeError("event payload floats must be finite")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise TypeError(f"event payload value is not JSON-compatible: {type(value).__name__}")


class InMemoryEventSink:
    """在内存中收集事件，并在并发情况下统一分配事件序号。"""

    def __init__(self, run_id: UUID, *, max_string_chars: int = 2_000) -> None:
        if max_string_chars < 1:
            raise ValueError("max_string_chars must be positive")
        self._run_id = run_id
        self._max_string_chars = max_string_chars
        self._events: list[RuntimeEvent] = []
        self._lock = asyncio.Lock()

    @property
    def events(self) -> tuple[RuntimeEvent, ...]:
        return tuple(self._events)

    async def emit(
        self, event_type: EventType, payload: Mapping[str, Any] | None = None
    ) -> RuntimeEvent:
        clean_payload = sanitize_payload(payload or {}, max_string_chars=self._max_string_chars)
        if not isinstance(clean_payload, dict):  # 防御性检查：emit 只接受映射类型的根节点。
            raise TypeError("event payload root must be a mapping")

        async with self._lock:
            event = RuntimeEvent(
                run_id=self._run_id,
                sequence=len(self._events) + 1,
                type=event_type,
                timestamp=datetime.now(UTC),
                payload=clean_payload,
            )
            self._events.append(event)
            return event
