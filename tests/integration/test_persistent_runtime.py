from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from evoagent.config import Settings
from evoagent.core.context import ContextBuilder
from evoagent.core.models import (
    FinishReason,
    Message,
    MessageRole,
    ModelResponse,
    ToolCall,
)
from evoagent.db.base import Base
from evoagent.db.models import RunEventRecord, RunRecord, TaskRecord
from evoagent.db.session import Database
from evoagent.providers.mock import MockProvider
from evoagent.runtime.persistent_runner import PersistentAgentRunner
from evoagent.runtime.recovery import RecoveryAction, RecoveryService
from evoagent.tasks.lease import JobLeaseManager
from evoagent.tasks.service import TaskService
from evoagent.tasks.state_machine import PersistentRunStatus, TaskStatus
from evoagent.tools.builtin.calculator import CalculatorTool
from evoagent.tools.registry import ToolRegistry
from evoagent.workers.main import JobWorker


@pytest.fixture
async def runtime_environment(tmp_path: Path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}",
        workspace=tmp_path / "workspace",
        lease_seconds=3,
        heartbeat_seconds=1,
    )
    service = TaskService(database.session_factory)
    session = await service.create_session("持久化运行")
    try:
        yield database, settings, service, session.id
    finally:
        await database.dispose()


def response(content: str) -> ModelResponse:
    return ModelResponse(
        message=Message(role=MessageRole.ASSISTANT, content=content),
        finish_reason=FinishReason.STOP,
    )


def runner(database: Database, settings: Settings, provider: MockProvider):
    return PersistentAgentRunner(
        settings=settings,
        session_factory=database.session_factory,
        context_builder=ContextBuilder(),
        provider=provider,
        registry=ToolRegistry([CalculatorTool()]),
    )


@pytest.mark.asyncio
async def test_worker_executes_persistent_runner_to_terminal_state(runtime_environment) -> None:
    database, settings, service, session_id = runtime_environment
    aggregate = await service.create_task(
        session_id=session_id,
        goal="直接回答",
        provider="mock",
        model="mock-model",
    )
    manager = JobLeaseManager(database.session_factory, lease_seconds=3)
    worker = JobWorker(
        worker_id="worker-a",
        lease_manager=manager,
        handler=runner(database, settings, MockProvider([response("持久化完成")])),
        heartbeat_seconds=1,
        poll_seconds=0.01,
    )

    assert await worker.run_once() is True

    async with database.session_factory() as session:
        task = await session.get(TaskRecord, aggregate.task.id)
        run = await session.get(RunRecord, aggregate.run.id)
        event_types = tuple(
            await session.scalars(
                select(RunEventRecord.event_type)
                .where(RunEventRecord.run_id == aggregate.run.id)
                .order_by(RunEventRecord.sequence)
            )
        )
    assert task is not None and task.status is TaskStatus.COMPLETED
    assert run is not None and run.status is PersistentRunStatus.COMPLETED
    assert run.final_answer == "持久化完成"
    assert event_types[-1] == "run.completed"


@pytest.mark.asyncio
async def test_recovery_resumes_from_latest_legal_snapshot(runtime_environment) -> None:
    database, settings, service, session_id = runtime_environment
    aggregate = await service.create_task(
        session_id=session_id,
        goal="先计算再回答",
        provider="mock",
        model="mock-model",
    )
    manager = JobLeaseManager(database.session_factory, lease_seconds=1)
    claimed_at = datetime.now(UTC)
    first_lease = await manager.claim_next("worker-crashed", now=claimed_at)
    assert first_lease is not None
    call = ToolCall(call_id="call-1", name="calculator", arguments={"expression": "20+22"})
    tool_response = ModelResponse(
        message=Message(role=MessageRole.ASSISTANT, tool_calls=(call,)),
        finish_reason=FinishReason.TOOL_CALLS,
    )

    failed_attempt = await runner(database, settings, MockProvider([tool_response])).handle(
        first_lease
    )
    assert failed_attempt.status is PersistentRunStatus.FAILED
    assert await manager.recover_expired(now=claimed_at + timedelta(seconds=2)) == 1

    decision = await RecoveryService(
        database.session_factory,
        snapshot_schema_version=1,
    ).recover(aggregate.task.id)
    assert decision.action is RecoveryAction.RESUME

    second_lease = await manager.claim_next("worker-replacement")
    assert second_lease is not None
    result = await runner(database, settings, MockProvider([response("答案是 42")])).handle(
        second_lease
    )
    await manager.finalize(second_lease, result)

    async with database.session_factory() as session:
        run = await session.get(RunRecord, aggregate.run.id)
        event_types = tuple(
            await session.scalars(
                select(RunEventRecord.event_type)
                .where(RunEventRecord.run_id == aggregate.run.id)
                .order_by(RunEventRecord.sequence)
            )
        )
    assert run is not None and run.final_answer == "答案是 42"
    assert "snapshot.saved" in event_types
    assert "recovery.started" in event_types
    assert "recovery.decided" in event_types
    assert "recovery.completed" in event_types
