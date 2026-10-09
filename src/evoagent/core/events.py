"""支持顺序编号和载荷脱敏的运行事件收集功能。"""

import asyncio
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import JsonValue

from evoagent.core.models import EventType, RuntimeEvent
from evoagent.privacy.redaction import redact_text

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


class ProgressEventType(StrEnum):
    MODEL_DELTA = "model.delta"


class InvalidProgressEvent(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ProgressReceipt:
    accepted_watermark: int
    durability: Literal["buffered", "committed"] = "buffered"


class RuntimeEventSink(Protocol):
    """接收同一次 Run 有序事件的目标接口。"""

    @property
    def events(self) -> tuple[RuntimeEvent, ...]: ...

    async def emit(
        self, event_type: EventType, payload: Mapping[str, Any] | None = None
    ) -> RuntimeEvent: ...

    async def append_progress(
        self, event_type: ProgressEventType, payload=None
    ) -> ProgressReceipt: ...

    async def flush(self) -> None: ...

    async def close(self) -> None: ...


def sanitize_payload(value: Any, *, max_string_chars: int) -> JsonValue:
    """移除敏感信息并截断过长字符串，返回与 JSON 兼容的值。

    自 §2.1 第 2 条起，**字符串值**也过共享敏感原语（键名结构脱敏、隐藏推理丢弃与
    截断规则仍是本模块自己的）。顺序是"先检测替换、再截断"：反过来会让跨越截断边界的
    秘密只被截掉一半。事件/日志保存的是脱敏投影，不是原始正文。
    """

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
        checked = redact_text(value)
        if len(checked) <= max_string_chars:
            return checked
        omitted = len(checked) - max_string_chars
        return f"{checked[:max_string_chars]}…[truncated {omitted} chars]"
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

    async def append_progress(self, event_type: ProgressEventType, payload=None) -> ProgressReceipt:
        if not isinstance(event_type, ProgressEventType):
            raise InvalidProgressEvent("only explicit MODEL_DELTA progress is accepted")
        event = await self.emit(EventType.MODEL_DELTA, payload)
        return ProgressReceipt(accepted_watermark=event.sequence, durability="committed")

    async def flush(self) -> None:
        pass

    async def close(self) -> None:
        pass

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
