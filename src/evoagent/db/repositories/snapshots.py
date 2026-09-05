"""RunSnapshot 的保存与最新快照查询。"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evoagent.db.models import RunSnapshotRecord


class RunSnapshotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, snapshot: RunSnapshotRecord) -> None:
        self._session.add(snapshot)

    async def latest(self, run_id: UUID) -> RunSnapshotRecord | None:
        return await self._session.scalar(
            select(RunSnapshotRecord)
            .where(RunSnapshotRecord.run_id == run_id)
            .order_by(RunSnapshotRecord.event_sequence.desc())
            .limit(1)
        )
