"""Task 聚合的应用服务与事务边界。"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.db.models import RunRecord, SessionRecord, TaskRecord
from evoagent.db.repositories.base import ConcurrentUpdateError, RecordNotFoundError
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.tasks.state_machine import PersistentRunStatus, TaskStatus


class TaskServiceError(Exception):
    """Task Service 可以安全映射成 API 响应的业务错误。"""

    code = "task_service_error"


class TaskNotFoundError(TaskServiceError):
    code = "task_not_found"


class SessionNotFoundError(TaskServiceError):
    code = "session_not_found"


class TaskOperationConflictError(TaskServiceError):
    code = "task_operation_conflict"


@dataclass(frozen=True, slots=True)
class TaskAggregate:
    """API 查询需要的 Task 与最新 Run 快照。"""

    task: TaskRecord
    run: RunRecord


class TaskService:
    """协调 Task、Run 与事件，避免路由层直接操作 ORM。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_session(self, title: str) -> SessionRecord:
        normalized = title.strip()
        if not normalized:
            raise ValueError("session title cannot be blank")
        async with UnitOfWork(self._session_factory) as unit:
            record = SessionRecord(title=normalized)
            unit.session.add(record)
            await unit.session.flush()
            await unit.commit()
            return record

    async def create_task(
        self,
        *,
        session_id: UUID,
        goal: str,
        provider: str,
        model: str,
    ) -> TaskAggregate:
        """在同一事务中创建 Task、首个 Run 和初始事件。"""

        normalized_goal = goal.strip()
        normalized_provider = provider.strip()
        normalized_model = model.strip()
        if not normalized_goal:
            raise ValueError("task goal cannot be blank")
        if not normalized_provider or not normalized_model:
            raise ValueError("provider and model cannot be blank")

        async with UnitOfWork(self._session_factory) as unit:
            if await unit.session.get(SessionRecord, session_id) is None:
                raise SessionNotFoundError(f"session does not exist: {session_id}")
            task = TaskRecord(
                session_id=session_id,
                goal=normalized_goal,
                status=TaskStatus.QUEUED,
            )
            unit.tasks.add(task)
            await unit.session.flush()
            run = RunRecord(
                task_id=task.id,
                status=PersistentRunStatus.QUEUED,
                provider=normalized_provider,
                model=normalized_model,
            )
            unit.runs.add(run)
            await unit.session.flush()
            await unit.events.append(
                run_id=run.id,
                event_type="task.queued",
                payload={"task_id": str(task.id)},
                created_at=datetime.now(UTC),
            )
            await unit.commit()
            return TaskAggregate(task=task, run=run)

    async def get_task(self, task_id: UUID) -> TaskAggregate:
        async with UnitOfWork(self._session_factory) as unit:
            try:
                task = await unit.tasks.get(task_id)
            except RecordNotFoundError as error:
                raise TaskNotFoundError(str(error)) from error
            run = await unit.runs.latest_for_task(task_id)
            if run is None:
                raise TaskNotFoundError(f"task has no run: {task_id}")
            return TaskAggregate(task=task, run=run)

    async def list_sessions(self) -> tuple[SessionRecord, ...]:
        async with self._session_factory() as session:
            records = await session.scalars(
                select(SessionRecord).order_by(SessionRecord.created_at, SessionRecord.id)
            )
            return tuple(records)

    async def cancel_task(self, task_id: UUID) -> TaskAggregate:
        """运行中的任务只记录取消请求，由持有租约的 Worker 收尾。"""

        async with UnitOfWork(self._session_factory) as unit:
            try:
                task = await unit.tasks.get(task_id)
            except RecordNotFoundError as error:
                raise TaskNotFoundError(str(error)) from error
            run = await unit.runs.latest_for_task(task_id)
            if run is None:
                raise TaskNotFoundError(f"task has no run: {task_id}")
            if task.status in (TaskStatus.RUNNING, TaskStatus.WAITING_TOOL):
                task.cancel_requested = True
                task.lock_version += 1
                await unit.events.append(
                    run_id=run.id,
                    event_type="task.cancel_requested",
                    payload={"task_id": str(task.id)},
                    created_at=datetime.now(UTC),
                )
                await unit.commit()
                return TaskAggregate(task=task, run=run)
        return await self._change_state(
            task_id,
            task_target=TaskStatus.CANCELLED,
            run_target=PersistentRunStatus.CANCELLED,
            event_type="task.cancelled",
        )

    async def pause_task(self, task_id: UUID) -> TaskAggregate:
        return await self._change_state(
            task_id,
            task_target=TaskStatus.PAUSED,
            run_target=PersistentRunStatus.PAUSED,
            event_type="task.paused",
        )

    async def resume_task(self, task_id: UUID) -> TaskAggregate:
        return await self._change_state(
            task_id,
            task_target=TaskStatus.QUEUED,
            run_target=PersistentRunStatus.QUEUED,
            event_type="task.resumed",
        )

    async def _change_state(
        self,
        task_id: UUID,
        *,
        task_target: TaskStatus,
        run_target: PersistentRunStatus,
        event_type: str,
    ) -> TaskAggregate:
        async with UnitOfWork(self._session_factory) as unit:
            try:
                task = await unit.tasks.get(task_id)
            except RecordNotFoundError as error:
                raise TaskNotFoundError(str(error)) from error
            run = await unit.runs.latest_for_task(task_id)
            if run is None:
                raise TaskNotFoundError(f"task has no run: {task_id}")
            try:
                task = await unit.tasks.transition(
                    task.id,
                    expected_version=task.lock_version,
                    target=task_target,
                )
                run = await unit.runs.transition(
                    run.id,
                    expected_version=run.lock_version,
                    target=run_target,
                )
            except (ConcurrentUpdateError, ValueError) as error:
                raise TaskOperationConflictError(str(error)) from error
            await unit.events.append(
                run_id=run.id,
                event_type=event_type,
                payload={"task_id": str(task.id), "status": task_target.value},
                created_at=datetime.now(UTC),
            )
            await unit.commit()
            return TaskAggregate(task=task, run=run)
