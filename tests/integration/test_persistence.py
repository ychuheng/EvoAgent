import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select

from evoagent.core.models import EventType
from evoagent.db.base import Base
from evoagent.db.models import RunEventRecord, RunRecord, SessionRecord, TaskRecord
from evoagent.db.repositories.base import ConcurrentUpdateError
from evoagent.db.session import Database
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.tasks.state_machine import PersistentRunStatus, TaskStatus
from evoagent.trace.persistent_sink import PersistentEventSink
from evoagent.trace.service import TraceService


@pytest.fixture
async def database(tmp_path: Path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield database
    finally:
        await database.dispose()


async def create_run(database: Database) -> tuple[TaskRecord, RunRecord]:
    async with UnitOfWork(database.session_factory) as unit:
        session = SessionRecord(title="测试会话")
        unit.session.add(session)
        await unit.session.flush()
        task = TaskRecord(session_id=session.id, goal="测试持久化", status=TaskStatus.QUEUED)
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
        return task, run


@pytest.mark.asyncio
async def test_unit_of_work_commits_and_rolls_back(database: Database) -> None:
    async with UnitOfWork(database.session_factory) as unit:
        committed = SessionRecord(title="已提交")
        unit.session.add(committed)
        await unit.commit()

    with pytest.raises(RuntimeError):
        async with UnitOfWork(database.session_factory) as unit:
            unit.session.add(SessionRecord(title="应回滚"))
            raise RuntimeError("触发回滚")

    async with database.session_factory() as session:
        titles = tuple(
            await session.scalars(select(SessionRecord.title).order_by(SessionRecord.title))
        )
    assert titles == ("已提交",)


@pytest.mark.asyncio
async def test_repositories_apply_state_machine_and_optimistic_lock(database: Database) -> None:
    task, run = await create_run(database)
    async with UnitOfWork(database.session_factory) as unit:
        updated_task = await unit.tasks.transition(
            task.id, expected_version=0, target=TaskStatus.RUNNING
        )
        updated_run = await unit.runs.transition(
            run.id,
            expected_version=0,
            target=PersistentRunStatus.RUNNING,
        )
        await unit.commit()

    assert updated_task.lock_version == 1
    assert updated_run.lock_version == 1

    async with UnitOfWork(database.session_factory) as unit:
        with pytest.raises(ConcurrentUpdateError):
            await unit.tasks.transition(
                task.id,
                expected_version=0,
                target=TaskStatus.COMPLETED,
            )


@pytest.mark.asyncio
async def test_persistent_event_sink_sanitizes_and_orders_events(database: Database) -> None:
    _, run = await create_run(database)
    sink = PersistentEventSink(run.id, database.session_factory)

    await asyncio.gather(
        sink.emit(EventType.MODEL_REQUESTED, {"api_key": "secret"}),
        sink.emit(EventType.MODEL_COMPLETED, {"answer": "done"}),
    )

    async with database.session_factory() as session:
        records = tuple(
            await session.scalars(
                select(RunEventRecord)
                .where(RunEventRecord.run_id == run.id)
                .order_by(RunEventRecord.sequence)
            )
        )
        stored_run = await session.get(RunRecord, run.id)

    assert [record.sequence for record in records] == [1, 2]
    assert records[0].payload["api_key"] == "[REDACTED]"
    assert stored_run is not None
    assert stored_run.next_event_sequence == 3
    assert [event.sequence for event in sink.events] == [1, 2]


@pytest.mark.asyncio
async def test_trace_service_returns_ordered_persisted_events(database: Database) -> None:
    _, run = await create_run(database)
    sink = PersistentEventSink(run.id, database.session_factory)
    await sink.emit(EventType.RUN_STARTED, {"model": "mock-model"})
    await sink.emit(EventType.RUN_COMPLETED, {"iterations": 1})

    trace = await TraceService(database.session_factory).get_run_trace(run.id)

    assert trace.run_id == run.id
    assert trace.status is PersistentRunStatus.QUEUED
    assert [event.event_type for event in trace.events] == ["run.started", "run.completed"]
