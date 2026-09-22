"""PostgreSQL 任务领取、租约续期与终态提交。"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.db.models import RunRecord, RuntimeEvalRunRecord, TaskRecord
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.tasks.lease_guard import LeaseGuard, database_now
from evoagent.tasks.lease_guard import LeaseLostError as LeaseLostError
from evoagent.tasks.state_machine import (
    PersistentRunStatus,
    TaskStatus,
    ensure_run_transition,
    ensure_task_transition,
)


class TaskCancellationRequested(RuntimeError):
    """当前租约对应的任务收到了取消请求。"""


@dataclass(frozen=True, slots=True)
class JobLease:
    task_id: UUID
    run_id: UUID
    owner: str
    expires_at: datetime
    attempt: int
    epoch: int = 0


@dataclass(frozen=True, slots=True)
class TaskExecutionResult:
    """TaskHandler 交给 Worker 持久化的执行结果。"""

    status: PersistentRunStatus
    final_answer: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    next_attempt_at: datetime | None = None


_RUN_TO_TASK_TERMINAL = {
    PersistentRunStatus.COMPLETED: TaskStatus.COMPLETED,
    PersistentRunStatus.FAILED: TaskStatus.FAILED,
    PersistentRunStatus.CANCELLED: TaskStatus.CANCELLED,
    PersistentRunStatus.TIMEOUT: TaskStatus.FAILED,
    PersistentRunStatus.LIMIT_REACHED: TaskStatus.FAILED,
}


class JobLeaseManager:
    """使用短事务领取任务，并用有期限的所有权代替长事务锁。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        lease_seconds: float,
        runtime_experiment_id=None,
        runtime_arm=None,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self._session_factory = session_factory
        self._lease_duration = timedelta(seconds=lease_seconds)
        self._runtime_experiment_id = runtime_experiment_id
        self._runtime_arm = runtime_arm

    async def claim_next(self, owner: str, *, now: datetime | None = None) -> JobLease | None:
        """用 ``SKIP LOCKED`` 领取一个到期可执行的 QUEUED Task。"""

        normalized_owner = owner.strip()
        if not normalized_owner:
            raise ValueError("lease owner cannot be blank")
        async with UnitOfWork(self._session_factory) as unit:
            current_time = now or await database_now(unit.session)
            runtime_tasks = select(RunRecord.task_id).join(
                RuntimeEvalRunRecord, RuntimeEvalRunRecord.run_id == RunRecord.id
            )
            statement = (
                select(TaskRecord)
                .where(
                    TaskRecord.id.in_(
                        runtime_tasks.where(
                            RuntimeEvalRunRecord.experiment_id == self._runtime_experiment_id,
                            RuntimeEvalRunRecord.arm == self._runtime_arm,
                        )
                    )
                    if self._runtime_experiment_id
                    else TaskRecord.id.not_in(runtime_tasks),
                    TaskRecord.status == TaskStatus.QUEUED,
                    or_(
                        TaskRecord.next_attempt_at.is_(None),
                        TaskRecord.next_attempt_at <= current_time,
                    ),
                    or_(
                        TaskRecord.lease_expires_at.is_(None),
                        TaskRecord.lease_expires_at <= current_time,
                    ),
                )
                .order_by(TaskRecord.created_at, TaskRecord.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            task = await unit.session.scalar(statement)
            if task is None:
                return None
            run = await unit.session.scalar(
                select(RunRecord)
                .where(RunRecord.task_id == task.id)
                .order_by(RunRecord.created_at.desc())
                .with_for_update()
                .limit(1)
            )
            if run is None:
                raise RuntimeError(f"task has no run: {task.id}")

            ensure_task_transition(task.status, TaskStatus.RUNNING)
            ensure_run_transition(run.status, PersistentRunStatus.RUNNING)
            expires_at = current_time + self._lease_duration
            task.status = TaskStatus.RUNNING
            task.lease_owner = normalized_owner
            task.lease_expires_at = expires_at
            task.heartbeat_at = current_time
            if run.error_code != "rate_limited":
                task.attempt_count += 1
            task.lease_epoch += 1
            task.lock_version += 1
            run.status = PersistentRunStatus.RUNNING
            run.started_at = run.started_at or current_time
            run.lock_version += 1
            await unit.events.append(
                run_id=run.id,
                event_type="worker.claimed",
                payload={
                    "worker_id": normalized_owner,
                    "attempt": task.attempt_count,
                    "lease_epoch": task.lease_epoch,
                },
                created_at=current_time,
            )
            await unit.commit()
            return JobLease(
                task_id=task.id,
                run_id=run.id,
                owner=normalized_owner,
                expires_at=expires_at,
                attempt=task.attempt_count,
                epoch=task.lease_epoch,
            )

    async def heartbeat(
        self,
        lease: JobLease,
        *,
        now: datetime | None = None,
    ) -> JobLease:
        """只有当前租约拥有者才能续期尚未过期的租约。"""

        async with self._session_factory() as session, session.begin():
            task, _ = await LeaseGuard(lease).check(session, now=now)
            current_time = now or await database_now(session)
            expires_at = current_time + self._lease_duration
            task.heartbeat_at = current_time
            task.lease_expires_at = expires_at
        return JobLease(
            task_id=lease.task_id,
            run_id=lease.run_id,
            owner=lease.owner,
            expires_at=expires_at,
            attempt=lease.attempt,
            epoch=lease.epoch,
        )

    async def cancellation_requested(self, lease: JobLease) -> bool:
        async with self._session_factory() as session:
            task, _ = await LeaseGuard(lease).check(session)
            return task.cancel_requested

    async def finalize(
        self,
        lease: JobLease,
        result: TaskExecutionResult,
        *,
        now: datetime | None = None,
    ) -> None:
        """检查租约所有权，并在一个事务中写入 Task/Run 终态与事件。"""

        if result.status is PersistentRunStatus.RETRYING:
            if result.next_attempt_at is None:
                raise ValueError("retrying result requires next_attempt_at")
            task_target = TaskStatus.RETRYING
        elif result.status is PersistentRunStatus.WAITING_USER:
            task_target = TaskStatus.WAITING_USER
        elif result.status in _RUN_TO_TASK_TERMINAL:
            task_target = _RUN_TO_TASK_TERMINAL[result.status]
        else:
            raise ValueError("execution result must be terminal or retrying")
        async with UnitOfWork(self._session_factory) as unit:
            current_time = now or await database_now(unit.session)
            task, run = await LeaseGuard(lease).check(unit.session, now=now)
            from evoagent.memory.repository import check_run_references
            from evoagent.memory.schema import MemoryError

            try:
                await check_run_references(unit.session, run.id)
            except MemoryError:
                result = TaskExecutionResult(
                    status=PersistentRunStatus.FAILED,
                    error_code="context_source_revoked",
                    error_message="memory source was revoked",
                )
                task_target = TaskStatus.FAILED
            ensure_task_transition(task.status, task_target)
            ensure_run_transition(run.status, result.status)
            task.status = task_target
            task.lease_owner = None
            task.lease_expires_at = None
            task.heartbeat_at = None
            task.next_attempt_at = result.next_attempt_at
            task.lock_version += 1
            run.status = result.status
            run.final_answer = result.final_answer
            run.error_code = result.error_code
            run.error_message = result.error_message
            run.ended_at = (
                None
                if result.status in (PersistentRunStatus.RETRYING, PersistentRunStatus.WAITING_USER)
                else current_time
            )
            run.lock_version += 1
            from evoagent.sessions.service import project_terminal

            await project_terminal(unit.session, task, run)
            await unit.events.append(
                run_id=run.id,
                event_type=(
                    "retry.scheduled"
                    if result.status is PersistentRunStatus.RETRYING
                    else "approval.waiting"
                    if result.status is PersistentRunStatus.WAITING_USER
                    else f"run.{result.status.value}"
                ),
                payload={
                    "error_code": result.error_code,
                    "error_message": result.error_message,
                    "next_attempt_at": (
                        result.next_attempt_at.isoformat()
                        if result.next_attempt_at is not None
                        else None
                    ),
                },
                created_at=current_time,
            )
            await unit.commit()

    async def recover_expired(self, *, now: datetime | None = None, limit: int = 100) -> int:
        """把失去心跳的运行中任务标为 RECOVERING，等待恢复服务决策。"""

        recovered = 0
        async with UnitOfWork(self._session_factory) as unit:
            current_time = now or await database_now(unit.session)
            tasks = tuple(
                await unit.session.scalars(
                    select(TaskRecord)
                    .where(
                        TaskRecord.status.in_((TaskStatus.RUNNING, TaskStatus.WAITING_TOOL)),
                        TaskRecord.lease_expires_at <= current_time,
                    )
                    .order_by(TaskRecord.lease_expires_at)
                    .with_for_update(skip_locked=True)
                    .limit(limit)
                )
            )
            for task in tasks:
                run = await unit.session.scalar(
                    select(RunRecord)
                    .where(RunRecord.task_id == task.id)
                    .order_by(RunRecord.created_at.desc())
                    .with_for_update()
                    .limit(1)
                )
                if run is None:
                    continue
                ensure_task_transition(task.status, TaskStatus.RECOVERING)
                ensure_run_transition(run.status, PersistentRunStatus.RECOVERING)
                previous_owner = task.lease_owner
                task.status = TaskStatus.RECOVERING
                task.lease_owner = None
                task.lease_expires_at = None
                task.heartbeat_at = None
                task.lock_version += 1
                run.status = PersistentRunStatus.RECOVERING
                run.lock_version += 1
                await unit.events.append(
                    run_id=run.id,
                    event_type="recovery.started",
                    payload={"reason": "lease_expired", "previous_owner": previous_owner},
                    created_at=current_time,
                )
                recovered += 1
            await unit.commit()
        return recovered

    async def promote_due_retries(self, *, now: datetime | None = None, limit: int = 100) -> int:
        """把退避时间已到的任务重新放回 QUEUED 队列。"""

        promoted = 0
        async with UnitOfWork(self._session_factory) as unit:
            current_time = now or await database_now(unit.session)
            tasks = tuple(
                await unit.session.scalars(
                    select(TaskRecord)
                    .where(
                        TaskRecord.status == TaskStatus.RETRYING,
                        TaskRecord.next_attempt_at <= current_time,
                    )
                    .order_by(TaskRecord.next_attempt_at)
                    .with_for_update(skip_locked=True)
                    .limit(limit)
                )
            )
            for task in tasks:
                run = await unit.session.scalar(
                    select(RunRecord)
                    .where(RunRecord.task_id == task.id)
                    .order_by(RunRecord.created_at.desc())
                    .with_for_update()
                    .limit(1)
                )
                if run is None:
                    continue
                ensure_task_transition(task.status, TaskStatus.QUEUED)
                ensure_run_transition(run.status, PersistentRunStatus.QUEUED)
                task.status = TaskStatus.QUEUED
                task.next_attempt_at = None
                task.lock_version += 1
                run.status = PersistentRunStatus.QUEUED
                run.lock_version += 1
                await unit.events.append(
                    run_id=run.id,
                    event_type="retry.ready",
                    payload={"attempt_count": task.attempt_count},
                    created_at=current_time,
                )
                promoted += 1
            await unit.commit()
        return promoted

    async def recover_pending(self, *, schema_version: int = 1) -> int:
        from evoagent.runtime.recovery import RecoveryService

        return await RecoveryService(
            self._session_factory, snapshot_schema_version=schema_version
        ).recover_pending()
