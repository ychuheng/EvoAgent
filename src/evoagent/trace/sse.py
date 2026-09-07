"""把已提交 RunEvent 投影为可续传的 SSE 流。"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID

from fastapi.sse import ServerSentEvent
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.db.unit_of_work import UnitOfWork
from evoagent.tasks.state_machine import PersistentRunStatus

_TERMINAL = {
    PersistentRunStatus.COMPLETED,
    PersistentRunStatus.FAILED,
    PersistentRunStatus.CANCELLED,
    PersistentRunStatus.TIMEOUT,
    PersistentRunStatus.LIMIT_REACHED,
}


class SseEventService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        poll_seconds: float,
        heartbeat_seconds: float,
    ) -> None:
        self._session_factory = session_factory
        self._poll_seconds = poll_seconds
        self._heartbeat_seconds = heartbeat_seconds

    async def stream(
        self, run_id: UUID, *, after_sequence: int = 0
    ) -> AsyncIterator[ServerSentEvent]:
        """从给定 sequence 后发送事件，历史补完且 Run 终止时关闭。"""

        cursor = after_sequence
        last_output = datetime.now(UTC)
        while True:
            async with UnitOfWork(self._session_factory) as unit:
                run = await unit.runs.get(run_id)
                records = await unit.events.list_for_run(run_id, after_sequence=cursor)
            for record in records:
                cursor = record.sequence
                last_output = datetime.now(UTC)
                yield ServerSentEvent(
                    id=str(record.sequence),
                    event=record.event_type,
                    data={
                        "sequence": record.sequence,
                        "type": record.event_type,
                        "payload": record.payload,
                        "created_at": record.created_at.isoformat(),
                    },
                )
            if run.status in _TERMINAL and not records:
                return
            if (datetime.now(UTC) - last_output).total_seconds() >= self._heartbeat_seconds:
                last_output = datetime.now(UTC)
                yield ServerSentEvent(comment="heartbeat")
            await asyncio.sleep(self._poll_seconds)
