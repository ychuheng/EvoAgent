from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from evoagent.core.events import InMemoryEventSink
from evoagent.core.models import ToolCall, ToolResultStatus
from evoagent.db.base import Base
from evoagent.db.models import (
    ApprovalStatus,
    ToolApprovalRecord,
    ToolCallRecord,
    ToolEffectRecord,
    ToolEffectStatus,
    TurnRecord,
)
from evoagent.db.session import Database
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.runtime.recovery import RecoveryAction, RecoveryService
from evoagent.tasks.lease import JobLeaseManager
from evoagent.tasks.service import TaskService
from evoagent.tools.approvals import ApprovalService
from evoagent.tools.builtin.file_write import FileWriteArguments, FileWriteTool
from evoagent.tools.effects import PersistentToolMiddleware
from evoagent.tools.executor import ToolExecutor
from evoagent.tools.policy import PermissionPolicy
from evoagent.tools.registry import ToolRegistry
from evoagent.tools.sandbox import RunSandbox


@pytest.mark.asyncio
async def test_crash_during_effect_requires_confirmation_before_retry(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'fault.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    service = TaskService(database.session_factory)
    session = await service.create_session("故障注入")
    aggregate = await service.create_task(
        session_id=session.id, goal="写入文件", provider="mock", model="mock-model"
    )
    manager = JobLeaseManager(database.session_factory, lease_seconds=1)
    claimed_at = datetime.now(UTC)
    lease = await manager.claim_next("worker-crashed", now=claimed_at)
    assert lease is not None

    call = ToolCall(
        call_id="effect-1",
        name="file_write",
        arguments={"path": "report.md", "content": "恢复成功"},
    )
    async with UnitOfWork(database.session_factory) as unit:
        turn = TurnRecord(run_id=aggregate.run.id, sequence=1, status="running")
        unit.session.add(turn)
        await unit.session.flush()
        call_record = ToolCallRecord(
            run_id=aggregate.run.id,
            turn_id=turn.id,
            provider_call_id=call.call_id,
            tool_name=call.name,
            arguments=dict(call.arguments),
            risk="R1",
        )
        unit.session.add(call_record)
        await unit.session.flush()
        unit.effects.add(
            ToolEffectRecord(
                tool_call_id=call_record.id,
                effect_scope=str(aggregate.task.id),
                semantic_key=PersistentToolMiddleware.semantic_key(
                    call.name,
                    FileWriteArguments.model_validate(call.arguments).model_dump(mode="json"),
                ),
                status=ToolEffectStatus.EXECUTING,
            )
        )
        await unit.commit()

    assert await manager.recover_expired(now=claimed_at + timedelta(seconds=2)) == 1
    decision = await RecoveryService(database.session_factory, snapshot_schema_version=1).recover(
        aggregate.task.id
    )
    assert decision.action is RecoveryAction.WAITING_CONFIRMATION

    async with database.session_factory() as db_session:
        effect = await db_session.scalar(select(ToolEffectRecord))
        approval = await db_session.scalar(select(ToolApprovalRecord))
    assert effect is not None and effect.status is ToolEffectStatus.UNKNOWN
    assert approval is not None and approval.status is ApprovalStatus.PENDING

    await ApprovalService(database.session_factory).decide(
        approval.id, approved=True, response="retry"
    )
    sandbox = RunSandbox(tmp_path / "artifacts", aggregate.run.id)
    executor = ToolExecutor(
        ToolRegistry([FileWriteTool(sandbox)]),
        InMemoryEventSink(aggregate.run.id),
        timeout_seconds=2,
        max_result_chars=1_000,
        middleware=PersistentToolMiddleware(
            task_id=aggregate.task.id,
            run_id=aggregate.run.id,
            session_factory=database.session_factory,
            policy=PermissionPolicy(),
        ),
    )
    result = await executor.execute(call)

    assert result.status is ToolResultStatus.SUCCESS
    assert (sandbox.root / "report.md").read_text(encoding="utf-8") == "恢复成功"
    async with database.session_factory() as db_session:
        effect = await db_session.scalar(select(ToolEffectRecord))
    assert effect is not None and effect.status is ToolEffectStatus.COMMITTED
    await database.dispose()
