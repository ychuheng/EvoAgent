import asyncio
import os

import pytest
from sqlalchemy import select

from evoagent.core.models import EventType
from evoagent.db.base import Base
from evoagent.db.models import RunEventRecord, RunRecord, SessionRecord, TaskRecord
from evoagent.db.session import Database
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.tasks.lease import JobLeaseManager
from evoagent.tasks.service import TaskService
from evoagent.tasks.state_machine import PersistentRunStatus, TaskStatus
from evoagent.trace.persistent_sink import PersistentEventSink


@pytest.fixture
async def postgres_database():
    database_url = os.getenv("EVOAGENT_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("EVOAGENT_TEST_DATABASE_URL is not configured")
    database = Database(database_url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield database
    finally:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await database.dispose()


async def create_postgres_run(database: Database) -> RunRecord:
    async with UnitOfWork(database.session_factory) as unit:
        session = SessionRecord(title="PostgreSQL 并发测试")
        unit.session.add(session)
        await unit.session.flush()
        task = TaskRecord(session_id=session.id, goal="并发追加事件", status=TaskStatus.QUEUED)
        unit.tasks.add(task)
        await unit.session.flush()
        run = RunRecord(
            task_id=task.id,
            status=PersistentRunStatus.QUEUED,
            provider="mock",
            model="mock-model",
        )
        unit.runs.add(run)
        await unit.commit()
        return run


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_two_persistent_sinks_allocate_unique_sequences(postgres_database: Database) -> None:
    run = await create_postgres_run(postgres_database)
    first = PersistentEventSink(run.id, postgres_database.session_factory)
    second = PersistentEventSink(run.id, postgres_database.session_factory)

    await asyncio.gather(
        *(
            (first if index % 2 == 0 else second).emit(
                EventType.MODEL_DELTA,
                {"index": index},
            )
            for index in range(20)
        )
    )

    async with postgres_database.session_factory() as session:
        records = tuple(
            await session.scalars(
                select(RunEventRecord)
                .where(RunEventRecord.run_id == run.id)
                .order_by(RunEventRecord.sequence)
            )
        )
        stored_run = await session.get(RunRecord, run.id)

    assert [record.sequence for record in records] == list(range(1, 21))
    assert stored_run is not None
    assert stored_run.next_event_sequence == 21


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_two_workers_cannot_claim_the_same_task(postgres_database: Database) -> None:
    service = TaskService(postgres_database.session_factory)
    session = await service.create_session("租约竞争")
    aggregate = await service.create_task(
        session_id=session.id,
        goal="只能被一个 Worker 领取",
        provider="mock",
        model="mock-model",
    )
    first = JobLeaseManager(postgres_database.session_factory, lease_seconds=30)
    second = JobLeaseManager(postgres_database.session_factory, lease_seconds=30)

    claims = await asyncio.gather(
        first.claim_next("worker-a"),
        second.claim_next("worker-b"),
    )

    leases = [lease for lease in claims if lease is not None]
    assert len(leases) == 1
    assert leases[0].task_id == aggregate.task.id


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_concurrent_artifact_identity_and_review(postgres_database, tmp_path):
    from evoagent.privacy.artifact_access import ArtifactInjectionGuard
    from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore

    service = TaskService(postgres_database.session_factory)
    session = await service.create_session("artifact identity")
    aggregate = await service.create_task(
        session_id=session.id, goal="test", provider="mock", model="mock"
    )
    store = LocalArtifactStore(tmp_path)
    artifacts = ArtifactService(store, postgres_database.session_factory)
    record = await artifacts.create_unique(
        run_id=aggregate.run.id,
        name="safe.txt",
        content=b"safe",
        artifact_type="tool_output",
        attributes={},
    )
    guard = ArtifactInjectionGuard(
        session_factory=postgres_database.session_factory, artifact_store=store
    )
    await asyncio.gather(
        *(
            guard._ensure_block_event(
                run_id=aggregate.run.id,
                source_id=record.id,
                source_hash=record.content_hash,
                categories=(),
                purpose="test",
            )
            for _ in range(8)
        )
    )
    request = dict(
        artifact_id=record.id,
        actor="human",
        reason="single review",
        expected_policy_version=None,
        expected_content_hash=record.content_hash,
        client_request_id="same",
        offline=True,
    )
    results = await asyncio.gather(*(guard.clear_quarantine(**request) for _ in range(4)))
    assert all(x.outcome == "cleared" for x in results)
    async with postgres_database.session_factory() as session:
        events = tuple(
            await session.scalars(
                select(RunEventRecord).where(RunEventRecord.run_id == aggregate.run.id)
            )
        )
        assert len([x for x in events if x.event_type == "artifact.injection_blocked"]) == 1
        assert (
            len([x for x in events if x.event_type == "artifact.quarantine_review_requested"]) == 1
        )
        assert len([x for x in events if x.event_type == "artifact.quarantine_cleared"]) == 1
