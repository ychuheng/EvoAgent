"""将持久化记录组装成稳定的 Run Trace。"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.db.unit_of_work import UnitOfWork
from evoagent.tasks.state_machine import PersistentRunStatus


class TraceEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int
    event_type: str
    payload: dict[str, Any]
    schema_version: int
    created_at: datetime


class RunTrace(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: UUID
    task_id: UUID
    status: PersistentRunStatus
    final_answer: str | None
    error_code: str | None
    events: tuple[TraceEvent, ...]


class TraceService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_run_trace(self, run_id: UUID) -> RunTrace:
        """查询 Run 当前状态和按 sequence 排序的完整事件。"""

        async with UnitOfWork(self._session_factory) as unit:
            run = await unit.runs.get(run_id)
            records = await unit.events.list_for_run(run_id)
        return RunTrace(
            run_id=run.id,
            task_id=run.task_id,
            status=run.status,
            final_answer=run.final_answer,
            error_code=run.error_code,
            events=tuple(
                TraceEvent(
                    sequence=record.sequence,
                    event_type=record.event_type,
                    payload=record.payload,
                    schema_version=record.schema_version,
                    created_at=record.created_at,
                )
                for record in records
            ),
        )
