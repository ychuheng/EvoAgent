"""RunEvent 的原子追加与有序查询。"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from evoagent.db.models import RunEventRecord, RunRecord
from evoagent.db.repositories.base import RecordNotFoundError


class RunEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(
        self,
        *,
        run_id: UUID,
        event_type: str,
        payload: dict[str, Any],
        created_at: datetime,
        schema_version: int = 1,
    ) -> RunEventRecord:
        """通过数据库原子计数器为事件分配唯一 sequence。"""

        statement = (
            update(RunRecord)
            .where(RunRecord.id == run_id)
            .values(next_event_sequence=RunRecord.next_event_sequence + 1)
            .returning(RunRecord.next_event_sequence)
        )
        next_sequence = (await self._session.execute(statement)).scalar_one_or_none()
        if next_sequence is None:
            raise RecordNotFoundError(f"run does not exist: {run_id}")
        event = RunEventRecord(
            run_id=run_id,
            sequence=next_sequence - 1,
            event_type=event_type,
            payload=payload,
            schema_version=schema_version,
            created_at=created_at,
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def list_for_run(
        self, run_id: UUID, *, after_sequence: int = 0
    ) -> tuple[RunEventRecord, ...]:
        result = await self._session.scalars(
            select(RunEventRecord)
            .where(
                RunEventRecord.run_id == run_id,
                RunEventRecord.sequence > after_sequence,
            )
            .order_by(RunEventRecord.sequence)
        )
        return tuple(result)
