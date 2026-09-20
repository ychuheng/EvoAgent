"""同事务租约 fencing；调用者必须在本事务内完成写入。"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from evoagent.db.models import RunRecord, TaskRecord
from evoagent.tasks.state_machine import PersistentRunStatus, TaskStatus

if TYPE_CHECKING:
    from evoagent.tasks.lease import JobLease


class LeaseLostError(RuntimeError):
    """本次执行已失去租约，禁止继续写入。"""


async def database_now(session: AsyncSession) -> datetime:
    if session.get_bind().dialect.name == "postgresql":
        return await session.scalar(select(func.clock_timestamp()))
    return datetime.now(UTC)


@dataclass(frozen=True)
class LeaseGuard:
    lease: "JobLease"

    async def check(
        self, session: AsyncSession, *, now: datetime | None = None
    ) -> tuple[TaskRecord, RunRecord]:
        # 固定 Task -> Run 锁序。SQLite 只用于功能验证，不提供行锁并发保证。
        task = await session.scalar(
            select(TaskRecord).where(TaskRecord.id == self.lease.task_id).with_for_update()
        )
        current = now or await database_now(session)
        expiry = task.lease_expires_at if task else None
        if expiry is not None and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        if (
            task is None
            or task.lease_owner != self.lease.owner
            or task.lease_epoch != self.lease.epoch
            or expiry is None
            or expiry <= current
            or task.status not in (TaskStatus.RUNNING, TaskStatus.WAITING_TOOL)
        ):
            raise LeaseLostError("lease expired or fenced by a newer execution")
        run = await session.scalar(
            select(RunRecord).where(RunRecord.id == self.lease.run_id).with_for_update()
        )
        if run is None or run.task_id != task.id or run.status is not PersistentRunStatus.RUNNING:
            raise LeaseLostError("run is no longer active under this lease")
        return task, run
