from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from evoagent.config import Settings
from evoagent.core.context import ContextBuilder
from evoagent.db.base import Base
from evoagent.db.models import RunEventRecord, RunRecord, TaskRecord
from evoagent.db.session import Database
from evoagent.providers.base import ProviderTimeoutError
from evoagent.providers.mock import MockProvider
from evoagent.runtime.persistent_runner import PersistentAgentRunner
from evoagent.runtime.retry import RetryPolicy
from evoagent.tasks.lease import JobLeaseManager
from evoagent.tasks.service import TaskService
from evoagent.tasks.state_machine import PersistentRunStatus, TaskStatus
from evoagent.tools.registry import ToolRegistry
from evoagent.workers.main import JobWorker


@pytest.mark.asyncio
async def test_transient_failure_is_scheduled_and_later_requeued(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'retry.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'retry.db'}",
        workspace=tmp_path / "workspace",
        lease_seconds=3,
        heartbeat_seconds=1,
    )
    service = TaskService(database.session_factory)
    session = await service.create_session("重试测试")
    aggregate = await service.create_task(
        session_id=session.id,
        goal="第一次请求超时",
        provider="mock",
        model="mock-model",
    )
    manager = JobLeaseManager(database.session_factory, lease_seconds=3)
    retry_policy = RetryPolicy(
        max_attempts=3,
        base_seconds=1,
        max_seconds=1,
        max_elapsed_seconds=30,
        max_total_tokens=100,
        random_source=__import__("random").Random(1),
    )
    runner = PersistentAgentRunner(
        settings=settings,
        session_factory=database.session_factory,
        context_builder=ContextBuilder(),
        provider=MockProvider([ProviderTimeoutError()]),
        registry=ToolRegistry(),
        retry_policy=retry_policy,
    )
    worker = JobWorker(
        worker_id="worker-a",
        lease_manager=manager,
        handler=runner,
        heartbeat_seconds=1,
        poll_seconds=0.01,
    )

    assert await worker.run_once() is True
    async with database.session_factory() as db_session:
        task = await db_session.get(TaskRecord, aggregate.task.id)
        run = await db_session.get(RunRecord, aggregate.run.id)
    assert task is not None and task.status is TaskStatus.RETRYING
    assert task.next_attempt_at is not None
    assert run is not None and run.status is PersistentRunStatus.RETRYING

    promoted = await manager.promote_due_retries(now=datetime.now(UTC) + timedelta(seconds=2))
    assert promoted == 1
    async with database.session_factory() as db_session:
        task = await db_session.get(TaskRecord, aggregate.task.id)
        event_types = tuple(
            await db_session.scalars(
                select(RunEventRecord.event_type)
                .where(RunEventRecord.run_id == aggregate.run.id)
                .order_by(RunEventRecord.sequence)
            )
        )
    assert task is not None and task.status is TaskStatus.QUEUED
    assert event_types[-2:] == ("retry.scheduled", "retry.ready")
    await database.dispose()
