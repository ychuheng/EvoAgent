from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from evoagent.core.models import ToolCall
from evoagent.db.base import Base
from evoagent.db.models import (
    ApprovalStatus,
    RunEventRecord,
    RunRecord,
    TaskRecord,
    ToolApprovalRecord,
    ToolEffectRecord,
    ToolEffectStatus,
)
from evoagent.db.session import Database
from evoagent.tasks.acceptance import AcceptanceSpec
from evoagent.tasks.lease import (
    JobLease,
    JobLeaseManager,
    LeaseLostError,
    TaskExecutionResult,
)
from evoagent.tasks.lease_guard import LeaseGuard
from evoagent.tasks.service import TaskService
from evoagent.tasks.state_machine import PersistentRunStatus, TaskStatus
from evoagent.tools.approvals import ApprovalService, ApprovalServiceError
from evoagent.tools.builtin.file_write import FileWriteTool
from evoagent.tools.effects import PersistentToolMiddleware
from evoagent.tools.policy import PermissionPolicy
from evoagent.tools.sandbox import RunSandbox


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
async def test_completed_result_cannot_bypass_explicit_acceptance(database: Database) -> None:
    service = TaskService(database.session_factory)
    session = await service.create_session("核验不能绕过")
    aggregate = await service.create_task(
        session_id=session.id,
        goal="需要实际验收",
        provider="mock",
        model="mock-model",
        acceptance=AcceptanceSpec(answer_contains=["目标值"]),
    )
    manager = JobLeaseManager(database.session_factory, lease_seconds=30)
    lease = await manager.claim_next("worker-a")
    assert lease is not None
    await manager.finalize(
        lease,
        TaskExecutionResult(status=PersistentRunStatus.COMPLETED, final_answer="随便回答"),
    )
    async with database.session_factory() as db:
        task = await db.get(TaskRecord, aggregate.task.id)
        run = await db.get(RunRecord, aggregate.run.id)
    assert task is not None and task.status is TaskStatus.FAILED
    assert run is not None and run.error_code == "acceptance_evidence_missing"
    assert run.final_answer == "随便回答"


@pytest.mark.asyncio
async def test_unknown_effect_blocks_completed_result_and_requires_user(
    database: Database, tmp_path: Path
) -> None:
    task, run = await create_queued_task(database)
    manager = JobLeaseManager(database.session_factory, lease_seconds=30)
    lease = await manager.claim_next("worker-a")
    assert lease is not None
    tool = FileWriteTool(RunSandbox(tmp_path / "files", run.id))
    call = ToolCall(call_id="uncertain", name="file_write", arguments={"path": "a", "content": "a"})
    middleware = PersistentToolMiddleware(
        task_id=task.id,
        run_id=run.id,
        session_factory=database.session_factory,
        policy=PermissionPolicy(),
        lease_guard=LeaseGuard(lease),
    )
    token = (await middleware.before(call, tool, tool.validate_arguments(call.arguments))).token
    await middleware.after_failure(token, "tool_timeout", "timed out after invocation")
    await manager.finalize(
        lease,
        TaskExecutionResult(status=PersistentRunStatus.COMPLETED, final_answer="已完成"),
    )
    async with database.session_factory() as session:
        stored_task = await session.get(TaskRecord, task.id)
        stored_run = await session.get(RunRecord, run.id)
        effect = await session.scalar(select(ToolEffectRecord))
        approval = await session.scalar(select(ToolApprovalRecord))
    assert stored_task is not None and stored_task.status is TaskStatus.WAITING_USER
    assert stored_run is not None and stored_run.status is PersistentRunStatus.WAITING_USER
    assert stored_run.error_code == "side_effect_unknown"
    assert effect is not None and effect.status is ToolEffectStatus.UNKNOWN
    assert approval is not None and approval.status is ApprovalStatus.PENDING

    with pytest.raises(ApprovalServiceError, match="outcome confirmation"):
        await ApprovalService(database.session_factory).decide(approval.id, approved=False)
    with pytest.raises(ApprovalServiceError, match="cannot replay"):
        await ApprovalService(database.session_factory).decide(
            approval.id, approved=True, response="retry"
        )
    await ApprovalService(database.session_factory).decide(
        approval.id, approved=True, response="committed:a"
    )
    async with database.session_factory() as session:
        settled_task = await session.get(TaskRecord, task.id)
        settled_effect = await session.get(ToolEffectRecord, effect.id)
    assert settled_task is not None and settled_task.status is TaskStatus.QUEUED
    assert settled_effect is not None and settled_effect.status is ToolEffectStatus.COMMITTED
    next_lease = await manager.claim_next("worker-b")
    assert next_lease is not None
    await manager.finalize(
        next_lease,
        TaskExecutionResult(status=PersistentRunStatus.COMPLETED, final_answer="已核对外部写入"),
    )
    async with database.session_factory() as session:
        completed = await session.get(TaskRecord, task.id)
    assert completed is not None and completed.status is TaskStatus.COMPLETED


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
