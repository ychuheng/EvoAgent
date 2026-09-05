"""把 RuntimeEvent 追加到数据库的 EventSink 实现。"""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.core.events import sanitize_payload
from evoagent.core.models import EventType, RuntimeEvent
from evoagent.db.unit_of_work import UnitOfWork


class PersistentEventSink:
    """每次 emit 使用短事务持久化事件，并保留本进程事件快照。"""

    def __init__(
        self,
        run_id: UUID,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        max_string_chars: int = 2_000,
    ) -> None:
        if max_string_chars < 1:
            raise ValueError("max_string_chars must be positive")
        self._run_id = run_id
        self._session_factory = session_factory
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
        if not isinstance(clean_payload, dict):
            raise TypeError("event payload root must be a mapping")

        async with self._lock:
            timestamp = datetime.now(UTC)
            async with UnitOfWork(self._session_factory) as unit:
                record = await unit.events.append(
                    run_id=self._run_id,
                    event_type=event_type.value,
                    payload=clean_payload,
                    created_at=timestamp,
                )
                await unit.commit()
            event = RuntimeEvent(
                run_id=self._run_id,
                sequence=record.sequence,
                type=event_type,
                timestamp=timestamp,
                payload=clean_payload,
            )
            self._events.append(event)
            return event
