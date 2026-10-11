"""把已提交 RunEvent 投影为可续传的 SSE 流。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import nullcontext
from datetime import UTC, datetime
from uuid import UUID

from fastapi.sse import ServerSentEvent
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.db.unit_of_work import UnitOfWork
from evoagent.tasks.state_machine import PersistentRunStatus
from evoagent.trace.notifications import RunEventNotifier

_TERMINAL = {
    PersistentRunStatus.COMPLETED,
    PersistentRunStatus.FAILED,
    PersistentRunStatus.CANCELLED,
    PersistentRunStatus.TIMEOUT,
    PersistentRunStatus.LIMIT_REACHED,
    PersistentRunStatus.AUTHORIZATION_REVOKED,
    PersistentRunStatus.INPUT_CHANGED,
}


class SseEventService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        poll_seconds: float,
        heartbeat_seconds: float,
        notifier: RunEventNotifier | None = None,
        page_size: int = 200,
        fallback_seconds: float = 2,
    ) -> None:
        self._session_factory = session_factory
        self._poll_seconds = poll_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._notifier = notifier
        if not 1 <= page_size <= 200 or not 0 < fallback_seconds <= 2:
            raise ValueError("invalid SSE replay budget")
        self._page_size, self._fallback_seconds = page_size, fallback_seconds

    async def stream(
        self, run_id: UUID, *, after_sequence: int = 0
    ) -> AsyncIterator[ServerSentEvent]:
        """从给定 sequence 后发送事件，历史补完且 Run 终止时关闭。"""

        cursor = after_sequence
        last_output = datetime.now(UTC)
        subscription = self._notifier.subscribe(run_id) if self._notifier else nullcontext(False)
        async with subscription:
            async for event in self._stream_subscribed(run_id, cursor, last_output):
                yield event

    async def _stream_subscribed(self, run_id, cursor, last_output):
        while True:
            generation = self._notifier.generation(run_id) if self._notifier else 0
            async with UnitOfWork(self._session_factory) as unit:
                run = await unit.runs.get(run_id)
                if self._notifier:
                    records = await unit.events.page_for_run(
                        run_id, after_sequence=cursor, limit=self._page_size
                    )
                else:
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
            if self._notifier and records:
                continue  # 积压立即补读，禁止每页额外 sleep。
            if (datetime.now(UTC) - last_output).total_seconds() >= self._heartbeat_seconds:
                last_output = datetime.now(UTC)
                yield ServerSentEvent(comment="heartbeat")
            if self._notifier:
                remaining = (
                    self._heartbeat_seconds - (datetime.now(UTC) - last_output).total_seconds()
                )
                await self._notifier.wait(
                    run_id, generation, min(self._fallback_seconds, max(0.001, remaining))
                )
            else:
                await asyncio.sleep(self._poll_seconds)
