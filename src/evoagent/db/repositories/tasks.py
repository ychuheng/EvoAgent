"""Task 聚合的持久化查询。"""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from evoagent.db.models import TaskRecord
from evoagent.db.repositories.base import ConcurrentUpdateError, RecordNotFoundError
from evoagent.tasks.state_machine import TaskStatus, ensure_task_transition


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, task: TaskRecord) -> None:
        self._session.add(task)

    async def get(self, task_id: UUID) -> TaskRecord:
        task = await self._session.get(TaskRecord, task_id)
        if task is None:
            raise RecordNotFoundError(f"task does not exist: {task_id}")
        return task

    async def transition(
        self,
        task_id: UUID,
        *,
        expected_version: int,
        target: TaskStatus,
    ) -> TaskRecord:
        """使用状态机与乐观锁原子迁移 Task。"""

        current = await self.get(task_id)
        ensure_task_transition(current.status, target)
        statement = (
            update(TaskRecord)
            .where(TaskRecord.id == task_id, TaskRecord.lock_version == expected_version)
            .values(status=target, lock_version=expected_version + 1)
            .returning(TaskRecord)
        )
        updated = (await self._session.execute(statement)).scalar_one_or_none()
        if updated is None:
            raise ConcurrentUpdateError(f"task was concurrently modified: {task_id}")
        return updated

    async def list_by_status(self, status: TaskStatus) -> tuple[TaskRecord, ...]:
        result = await self._session.scalars(
            select(TaskRecord).where(TaskRecord.status == status).order_by(TaskRecord.created_at)
        )
        return tuple(result)
