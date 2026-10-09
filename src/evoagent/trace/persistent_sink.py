"""把 RuntimeEvent 追加到数据库的 EventSink 实现。"""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.core.events import (
    InvalidProgressEvent,
    ProgressEventType,
    ProgressReceipt,
    sanitize_payload,
)
from evoagent.core.models import EventType, RuntimeEvent
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.memory.repository import check_run_references
from evoagent.memory.schema import MemoryError
from evoagent.skills.canonical import canonical_json
from evoagent.tasks.lease_guard import LeaseGuard


class PersistentEventSink:
    """每次 emit 使用短事务持久化事件，并保留本进程事件快照。"""

    def __init__(
        self,
        run_id: UUID,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        max_string_chars: int = 2_000,
        lease_guard: LeaseGuard | None = None,
        batch_progress: bool = False,
        on_background_error=None,
    ) -> None:
        if max_string_chars < 1:
            raise ValueError("max_string_chars must be positive")
        if lease_guard is not None and lease_guard.lease.run_id != run_id:
            raise ValueError("run does not match lease")
        self._lease_guard = lease_guard
        self._run_id = run_id
        self._session_factory = session_factory
        self._max_string_chars = max_string_chars
        self._events: list[RuntimeEvent] = []
        self._lock = asyncio.Lock()
        self._batch_progress = batch_progress
        self._pending: list[dict] = []
        self._pending_bytes = 0
        self._timer: asyncio.Task | None = None
        self._background_error: BaseException | None = None
        self._on_background_error = on_background_error
        self._accepted_watermark = 0
        self._flushed_watermark = 0
        self._closed = False

    async def append_progress(self, event_type: ProgressEventType, payload=None) -> ProgressReceipt:
        if not isinstance(event_type, ProgressEventType):
            raise InvalidProgressEvent("only explicit MODEL_DELTA progress is accepted")
        if not self._batch_progress:
            await self.emit(EventType.MODEL_DELTA, payload)
            self._accepted_watermark += 1
            self._flushed_watermark = self._accepted_watermark
            return ProgressReceipt(self._accepted_watermark, "committed")
        clean = sanitize_payload(payload or {}, max_string_chars=self._max_string_chars)
        if not isinstance(clean, dict):
            raise TypeError("event payload root must be a mapping")
        size = len(canonical_json(clean).encode())
        async with self._lock:
            self._raise_background_error()
            if size > 64 * 1024:
                raise ValueError("progress payload exceeds batch byte budget")
            if self._pending_bytes + size > 64 * 1024:
                await self._flush_locked()
            self._pending.append(
                dict(event_type=event_type.value, payload=clean, created_at=datetime.now(UTC))
            )
            self._pending_bytes += size
            self._accepted_watermark += 1
            if len(self._pending) >= 32 or self._pending_bytes >= 64 * 1024:
                await self._flush_locked()
            elif self._timer is None or self._timer.done():
                self._timer = asyncio.create_task(self._flush_after_delay())
            return ProgressReceipt(self._accepted_watermark)

    def _raise_background_error(self):
        if self._closed:
            raise RuntimeError("event sink is closed")
        if self._background_error is not None:
            raise self._background_error

    async def _flush_after_delay(self):
        try:
            await asyncio.sleep(0.1)
            async with self._lock:
                await self._flush_locked()
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            # 不留下无人读取的 Task 异常；下个持久化边界显式失败。
            self._background_error = error
            if self._on_background_error is not None:
                self._on_background_error(error)

    async def _flush_locked(self):
        if not self._pending:
            return
        try:
            async with UnitOfWork(self._session_factory) as unit:
                if self._lease_guard is not None:
                    await self._lease_guard.check(unit.session)
                    try:
                        await check_run_references(unit.session, self._run_id)
                    except MemoryError:
                        for item in self._pending:
                            item["payload"] = {"context_source_revoked": True}
                records = await unit.events.append_many(self._run_id, self._pending)
                await unit.commit()
        except BaseException as error:
            # 包括提交结果不确定与 fencing 失败：终止本 sink，绝不自动重放。
            self._background_error = error
            self._pending.clear()
            self._pending_bytes = 0
            raise
        for record in records:
            self._events.append(
                RuntimeEvent(
                    run_id=self._run_id,
                    sequence=record.sequence,
                    type=EventType(record.event_type),
                    timestamp=record.created_at,
                    payload=record.payload,
                )
            )
        self._pending.clear()
        self._pending_bytes = 0
        self._flushed_watermark = self._accepted_watermark

    async def flush(self):
        async with self._lock:
            self._raise_background_error()
            await self._flush_locked()

    async def aclose(self):
        try:
            # 等正在进行的事务结束，再关闭；不取消在途 COMMIT 并盲目重放。
            async with self._lock:
                if self._closed:
                    return
                try:
                    self._raise_background_error()
                    await self._flush_locked()
                finally:
                    self._closed = True
        finally:
            await self._cancel_timer()

    async def _cancel_timer(self):
        if self._timer is not None and not self._timer.done():
            self._timer.cancel()
            await asyncio.gather(self._timer, return_exceptions=True)

    async def close(self):
        await self.aclose()

    async def abort(self):
        # 租约丢失时只丢弃未提交进度，禁止后续持久化与重新接收。
        async with self._lock:
            self._closed = True
            self._pending.clear()
            self._pending_bytes = 0
        await self._cancel_timer()

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
            self._raise_background_error()
            await self._flush_locked()
            timestamp = datetime.now(UTC)
            async with UnitOfWork(self._session_factory) as unit:
                if self._lease_guard is not None:
                    await self._lease_guard.check(unit.session)
                    try:
                        await check_run_references(unit.session, self._run_id)
                    except MemoryError:
                        clean_payload = {"context_source_revoked": True}
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
