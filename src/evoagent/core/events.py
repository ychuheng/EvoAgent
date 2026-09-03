"""Runtime event collection with ordering and payload redaction."""

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
    """Destination for ordered events belonging to one Run."""

    @property
    def events(self) -> tuple[RuntimeEvent, ...]: ...

    async def emit(
        self, event_type: EventType, payload: Mapping[str, Any] | None = None
    ) -> RuntimeEvent: ...


def sanitize_payload(value: Any, *, max_string_chars: int) -> JsonValue:
    """Return a JSON-compatible value with secrets removed and large strings shortened."""

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
    """Collect events in memory and assign a single sequence under concurrency."""

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
        if not isinstance(clean_payload, dict):  # Defensive: emit accepts only a mapping root.
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
