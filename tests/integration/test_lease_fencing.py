import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from evoagent.config import Settings
from evoagent.core.context import ContextBuilder
from evoagent.core.models import EventType, LoopState, ToolCall
from evoagent.db.base import Base
from evoagent.db.models import (
    ApprovalStatus,
    ArtifactRecord,
    RunEventRecord,
    RunRecord,
    RunSnapshotRecord,
    TaskRecord,
    ToolApprovalRecord,
    ToolCallRecord,
    ToolEffectRecord,
    ToolEffectStatus,
)
from evoagent.db.session import Database
from evoagent.providers.mock import MockProvider
from evoagent.runtime.checkpoints import PersistentCheckpointStore
from evoagent.runtime.persistent_runner import PersistentAgentRunner
from evoagent.skills.retrieval import SkillRetrievalService
from evoagent.tasks.lease import JobLeaseManager, LeaseLostError, TaskExecutionResult
from evoagent.tasks.lease_guard import LeaseGuard
from evoagent.tasks.service import TaskService
from evoagent.tasks.state_machine import PersistentRunStatus, TaskStatus
from evoagent.tools.approvals import ApprovalService
from evoagent.tools.builtin.file_write import FileWriteTool
from evoagent.tools.effects import PersistentToolMiddleware
from evoagent.tools.policy import PermissionPolicy
from evoagent.tools.registry import ToolRegistry
from evoagent.tools.sandbox import RunSandbox
from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore
from evoagent.trace.persistent_sink import PersistentEventSink
from evoagent.workers.main import JobWorker


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
async def fencing_db(request, tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'fencing.db'}"
    if request.param == "postgres":
        url = os.getenv("EVOAGENT_TEST_DATABASE_URL")
        if not url:
            pytest.skip("EVOAGENT_TEST_DATABASE_URL is not configured")
    database = Database(url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield database
    finally:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await database.dispose()


async def setup_task(database):
    service = TaskService(database.session_factory)
    session = await service.create_session("fencing")
    aggregate = await service.create_task(
        session_id=session.id, goal="write", provider="mock", model="mock-model"
    )
    manager = JobLeaseManager(database.session_factory, lease_seconds=30)
    lease = await manager.claim_next("same-label")
    return aggregate, manager, lease


async def counts(database):
    async with database.session_factory() as session:
        return tuple(
            [
                await session.scalar(select(func.count()).select_from(table))
                for table in (
                    RunEventRecord,
                    RunSnapshotRecord,
                    ToolCallRecord,
                    ToolEffectRecord,
                    ToolApprovalRecord,
                    ArtifactRecord,
                )
            ]
        )


async def test_old_epoch_cannot_write_any_runtime_fact(fencing_db, tmp_path):
    database = fencing_db
    aggregate, manager, old = await setup_task(database)
    guard = LeaseGuard(old)
    tool = FileWriteTool(RunSandbox(tmp_path / "files", old.run_id))
    call = ToolCall(call_id="a", name="file_write", arguments={"path": "a", "content": "a"})
    middleware = PersistentToolMiddleware(
        task_id=old.task_id,
        run_id=old.run_id,
        session_factory=database.session_factory,
        policy=PermissionPolicy(),
        lease_guard=guard,
    )
    token = (await middleware.before(call, tool, tool.validate_arguments(call.arguments))).token
    await middleware.after_success(token, "committed")
    # A loses its lease; normal recovery and claim give B the same label but a new epoch.
    await manager.recover_expired(now=datetime.now(UTC) + timedelta(seconds=60))
    await manager.recover_pending()
    new = await manager.claim_next("same-label")
    assert new.epoch == old.epoch + 1
    assert new.owner == old.owner
    before = await counts(database)
    checkpoint = PersistentCheckpointStore(old.run_id, database.session_factory, lease_guard=guard)
    state = LoopState(
        messages=ContextBuilder().build("task"),
        completed_iterations=0,
        usage_is_complete=False,
        config_hash="test",
    )
    registry = ToolRegistry([tool])
    artifacts = ArtifactService(
        LocalArtifactStore(tmp_path / "artifacts"), database.session_factory, lease_guard=guard
    )
    runner = PersistentAgentRunner(
        settings=Settings(_env_file=None, workspace=tmp_path),
        session_factory=database.session_factory,
        context_builder=ContextBuilder(),
        provider=MockProvider([]),
        registry=registry,
    )
    operations = [
        lambda: PersistentEventSink(old.run_id, database.session_factory, lease_guard=guard).emit(
            EventType.MODEL_DELTA
        ),
        lambda: checkpoint.save(state),
        lambda: middleware.before(call, tool, tool.validate_arguments(call.arguments)),
        lambda: middleware.after_success(token, "late result"),
        lambda: middleware.after_failure(token, "timeout", "late failure"),
        lambda: SkillRetrievalService(database.session_factory, registry, lease_guard=guard).select(
            old.run_id, "new query"
        ),
        lambda: runner._persist_run_config(old.run_id, None, guard),
        lambda: artifacts.create(
            run_id=old.run_id, name="late", content=b"late", artifact_type="text"
        ),
        lambda: manager.heartbeat(old),
        lambda: manager.finalize(old, TaskExecutionResult(status=PersistentRunStatus.COMPLETED)),
    ]
    for operation in operations:
        with pytest.raises(LeaseLostError):
            await operation()
    assert await counts(database) == before
    assert not (tmp_path / "artifacts" / str(old.run_id) / "late").exists()
    await PersistentEventSink(
        new.run_id, database.session_factory, lease_guard=LeaseGuard(new)
    ).emit(EventType.MODEL_DELTA)
    assert (await manager.heartbeat(new)).epoch == new.epoch
    # Correct epoch does not permit a mismatched Run.
    with pytest.raises(LeaseLostError):
        await manager.heartbeat(replace(new, run_id=aggregate.task.id))


async def test_recovery_requires_all_unknown_effect_confirmations(fencing_db, tmp_path):
    database = fencing_db
    _, manager, lease = await setup_task(database)
    tool = FileWriteTool(RunSandbox(tmp_path / "files", lease.run_id))
    middleware = PersistentToolMiddleware(
        task_id=lease.task_id,
        run_id=lease.run_id,
        session_factory=database.session_factory,
        policy=PermissionPolicy(),
        lease_guard=LeaseGuard(lease),
    )
    for index in range(2):
        call = ToolCall(
            call_id=str(index), name="file_write", arguments={"path": str(index), "content": "x"}
        )
        await middleware.before(call, tool, tool.validate_arguments(call.arguments))
    await manager.recover_expired(now=datetime.now(UTC) + timedelta(seconds=60))
    assert await manager.recover_pending() == 1
    assert await manager.recover_pending() == 0
    async with database.session_factory() as session:
        approvals = tuple(await session.scalars(select(ToolApprovalRecord)))
        effects = tuple(await session.scalars(select(ToolEffectRecord)))
    assert len(approvals) == 2
    assert all(a.status == ApprovalStatus.PENDING for a in approvals)
    assert all(e.status == ToolEffectStatus.UNKNOWN for e in effects)
    service = ApprovalService(database.session_factory)
    await service.decide(approvals[0].id, approved=True, response="retry")
    async with database.session_factory() as session:
        assert (await session.get(TaskRecord, lease.task_id)).status == TaskStatus.WAITING_USER
    assert await manager.claim_next("waiting") is None
    await service.decide(approvals[1].id, approved=True, response="committed:done")
    assert await manager.claim_next("ready") is not None


async def test_worker_identity_is_unique_and_cancellation_awaits_handler_cleanup(fencing_db):
    _, manager, lease = await setup_task(fencing_db)
    await manager.recover_expired(now=datetime.now(UTC) + timedelta(seconds=60))
    entered, cleaned = asyncio.Event(), asyncio.Event()

    class Handler:
        async def handle(self, lease):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.set()

    def worker():
        return JobWorker(
            worker_id="same",
            lease_manager=manager,
            handler=Handler(),
            heartbeat_seconds=0.1,
            poll_seconds=0.01,
        )

    first, second = worker(), worker()
    assert first._worker_id != second._worker_id
    task = asyncio.create_task(first.run_once())
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()


async def test_zero_hit_selection_is_frozen_before_config_commit(fencing_db):
    _, _, lease = await setup_task(fencing_db)
    service = SkillRetrievalService(
        fencing_db.session_factory, ToolRegistry(), lease_guard=LeaseGuard(lease)
    )
    assert await service.select(lease.run_id, "nothing") == ()
    before = await counts(fencing_db)
    assert await service.select(lease.run_id, "different query") == ()
    assert await counts(fencing_db) == before
    async with fencing_db.session_factory() as session:
        run = await session.get(RunRecord, lease.run_id)
        assert run.skill_selection_frozen and run.config_snapshot is None


async def test_guard_and_write_share_lock_until_commit(fencing_db):
    if fencing_db.engine.dialect.name != "postgresql":
        pytest.skip("row lock semantics require PostgreSQL")
    _, manager, lease = await setup_task(fencing_db)
    async with fencing_db.session_factory() as session:
        await LeaseGuard(lease).check(session)
        # SKIP LOCKED recovery cannot take over while the guarded write transaction owns Task.
        assert await manager.recover_expired(now=datetime.now(UTC) + timedelta(seconds=60)) == 0
        await session.commit()
    assert await manager.recover_expired(now=datetime.now(UTC) + timedelta(seconds=60)) == 1
