from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from evoagent.db.base import Base
from evoagent.db.models import RunEventRecord, RunRecord, TaskRecord
from evoagent.db.session import Database
from evoagent.tasks.lease import (
    JobLease,
    JobLeaseManager,
    LeaseLostError,
    TaskExecutionResult,
)
from evoagent.tasks.service import TaskService
from evoagent.tasks.state_machine import PersistentRunStatus, TaskStatus


@pytest.fixture
async def database(tmp_path: Path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'lease.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield database
    finally:
        await database.dispose()


async def create_queued_task(database: Database) -> tuple[TaskRecord, RunRecord]:
    service = TaskService(database.session_factory)
    session = await service.create_session("租约测试")
    aggregate = await service.create_task(
        session_id=session.id,
        goal="等待 Worker 领取",
        provider="mock",
        model="mock-model",
    )
    return aggregate.task, aggregate.run


@pytest.mark.asyncio
async def test_claim_heartbeat_and_finalize(database: Database) -> None:
    task, run = await create_queued_task(database)
    manager = JobLeaseManager(database.session_factory, lease_seconds=30)
    now = datetime.now(UTC)

    lease = await manager.claim_next("worker-a", now=now)
    assert lease is not None
    renewed = await manager.heartbeat(lease, now=now + timedelta(seconds=5))
    await manager.finalize(
        renewed,
        TaskExecutionResult(
            status=PersistentRunStatus.COMPLETED,
            final_answer="处理完成",
        ),
        now=now + timedelta(seconds=6),
    )

    async with database.session_factory() as session:
        stored_task = await session.get(TaskRecord, task.id)
        stored_run = await session.get(RunRecord, run.id)
        event_types = tuple(
            await session.scalars(
                select(RunEventRecord.event_type)
                .where(RunEventRecord.run_id == run.id)
                .order_by(RunEventRecord.sequence)
            )
        )
    assert stored_task is not None and stored_task.status is TaskStatus.COMPLETED
    assert stored_task.lease_owner is None
    assert stored_run is not None and stored_run.status is PersistentRunStatus.COMPLETED
    assert stored_run.final_answer == "处理完成"
    assert event_types == ("task.queued", "worker.claimed", "run.completed")


@pytest.mark.asyncio
async def test_wrong_owner_cannot_heartbeat_or_finalize(database: Database) -> None:
    await create_queued_task(database)
    manager = JobLeaseManager(database.session_factory, lease_seconds=30)
    lease = await manager.claim_next("worker-a")
    assert lease is not None
    stolen = JobLease(
        task_id=lease.task_id,
        run_id=lease.run_id,
        owner="worker-b",
        expires_at=lease.expires_at,
        attempt=lease.attempt,
    )

    with pytest.raises(LeaseLostError):
        await manager.heartbeat(stolen)
    with pytest.raises(LeaseLostError):
        await manager.finalize(
            stolen,
            TaskExecutionResult(status=PersistentRunStatus.FAILED, error_code="x"),
        )


@pytest.mark.asyncio
async def test_expired_lease_enters_recovering(database: Database) -> None:
    task, run = await create_queued_task(database)
    manager = JobLeaseManager(database.session_factory, lease_seconds=1)
    now = datetime.now(UTC)
    assert await manager.claim_next("worker-a", now=now) is not None

    count = await manager.recover_expired(now=now + timedelta(seconds=2))

    async with database.session_factory() as session:
        stored_task = await session.get(TaskRecord, task.id)
        stored_run = await session.get(RunRecord, run.id)
    assert count == 1
    assert stored_task is not None and stored_task.status is TaskStatus.RECOVERING
    assert stored_task.lease_owner is None
    assert stored_run is not None and stored_run.status is PersistentRunStatus.RECOVERING
