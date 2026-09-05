"""Run 聚合的持久化查询。"""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from evoagent.db.models import RunRecord
from evoagent.db.repositories.base import ConcurrentUpdateError, RecordNotFoundError
from evoagent.tasks.state_machine import PersistentRunStatus, ensure_run_transition


class RunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, run: RunRecord) -> None:
        self._session.add(run)

    async def get(self, run_id: UUID) -> RunRecord:
        run = await self._session.get(RunRecord, run_id)
        if run is None:
            raise RecordNotFoundError(f"run does not exist: {run_id}")
        return run

    async def latest_for_task(self, task_id: UUID) -> RunRecord | None:
        return await self._session.scalar(
            select(RunRecord)
            .where(RunRecord.task_id == task_id)
            .order_by(RunRecord.created_at.desc())
            .limit(1)
        )

    async def transition(
        self,
        run_id: UUID,
        *,
        expected_version: int,
        target: PersistentRunStatus,
    ) -> RunRecord:
        """使用状态机与乐观锁原子迁移 Run。"""

        current = await self.get(run_id)
        ensure_run_transition(current.status, target)
        statement = (
            update(RunRecord)
            .where(RunRecord.id == run_id, RunRecord.lock_version == expected_version)
            .values(status=target, lock_version=expected_version + 1)
            .returning(RunRecord)
        )
        updated = (await self._session.execute(statement)).scalar_one_or_none()
        if updated is None:
            raise ConcurrentUpdateError(f"run was concurrently modified: {run_id}")
        return updated
