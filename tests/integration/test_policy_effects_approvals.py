from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from evoagent.api.app import create_app
from evoagent.config import Settings
from evoagent.core.events import InMemoryEventSink
from evoagent.core.models import ToolCall, ToolResultStatus
from evoagent.db.base import Base
from evoagent.db.models import (
    ApprovalStatus,
    ToolApprovalRecord,
    ToolCallRecord,
    ToolCallStatus,
    ToolEffectRecord,
    ToolEffectStatus,
)
from evoagent.db.session import Database
from evoagent.tasks.service import TaskService
from evoagent.tools.approvals import ApprovalRequiredError, ApprovalService
from evoagent.tools.builtin.ask_user import AskUserTool
from evoagent.tools.builtin.file_write import FileWriteTool
from evoagent.tools.effects import PersistentToolMiddleware
from evoagent.tools.executor import ToolExecutor
from evoagent.tools.policy import PermissionPolicy
from evoagent.tools.registry import ToolRegistry
from evoagent.tools.sandbox import RunSandbox


@pytest.mark.asyncio
async def test_committed_side_effect_is_reused_without_second_write(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'effects.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    service = TaskService(database.session_factory)
    session = await service.create_session("副作用测试")
    aggregate = await service.create_task(
        session_id=session.id, goal="写报告", provider="mock", model="mock-model"
    )
    sandbox = RunSandbox(tmp_path / "artifacts", aggregate.run.id)
    registry = ToolRegistry([FileWriteTool(sandbox)])
    middleware = PersistentToolMiddleware(
        task_id=aggregate.task.id,
        run_id=aggregate.run.id,
        session_factory=database.session_factory,
        policy=PermissionPolicy(),
    )
    executor = ToolExecutor(
        registry,
        InMemoryEventSink(aggregate.run.id),
        timeout_seconds=2,
        max_result_chars=1_000,
        middleware=middleware,
    )
    call = ToolCall(
        call_id="write-1",
        name="file_write",
        arguments={"path": "report.md", "content": "第一版"},
    )

    first = await executor.execute(call)
    repeated_call = ToolCall(
        call_id="write-replayed",
        name="file_write",
        arguments={"path": "report.md", "content": "第一版"},
    )
    second = await executor.execute(repeated_call)

    assert first.status is ToolResultStatus.SUCCESS
    assert second.content == first.content
    assert (sandbox.root / "report.md").read_text(encoding="utf-8") == "第一版"
    async with database.session_factory() as db_session:
        effects = tuple(await db_session.scalars(select(ToolEffectRecord)))
        replayed_record = await db_session.scalar(
            select(ToolCallRecord).where(ToolCallRecord.provider_call_id == "write-replayed")
        )
    assert len(effects) == 1
    assert effects[0].status is ToolEffectStatus.COMMITTED
    assert replayed_record is not None and replayed_record.status is ToolCallStatus.SUCCEEDED
    await database.dispose()


@pytest.mark.asyncio
async def test_r2_call_creates_approval_and_records_decision(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'approval.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    service = TaskService(database.session_factory)
    session = await service.create_session("审批测试")
    aggregate = await service.create_task(
        session_id=session.id, goal="覆盖报告", provider="mock", model="mock-model"
    )
    sandbox = RunSandbox(tmp_path / "artifacts", aggregate.run.id)
    (sandbox.root / "report.md").write_text("旧内容", encoding="utf-8")
    registry = ToolRegistry([FileWriteTool(sandbox)])
    middleware = PersistentToolMiddleware(
        task_id=aggregate.task.id,
        run_id=aggregate.run.id,
        session_factory=database.session_factory,
        policy=PermissionPolicy(),
    )
    executor = ToolExecutor(
        registry,
        InMemoryEventSink(uuid4()),
        timeout_seconds=2,
        max_result_chars=1_000,
        middleware=middleware,
    )
    call = ToolCall(
        call_id="overwrite-1",
        name="file_write",
        arguments={"path": "report.md", "content": "新内容", "overwrite": True},
    )

    with pytest.raises(ApprovalRequiredError) as raised:
        await executor.execute(call)
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'approval.db'}",
            workspace=tmp_path / "workspace",
        ),
        database=database,
    )
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        pending = await client.get(f"/api/v1/tool-approvals/{raised.value.approval_id}")
        decided = await client.post(
            f"/api/v1/tool-approvals/{raised.value.approval_id}/approve",
            json={},
        )
    assert pending.status_code == 200
    assert pending.json()["status"] == "pending"
    assert decided.status_code == 200
    assert decided.json()["status"] == "approved"
    approval = await ApprovalService(database.session_factory).get(raised.value.approval_id)
    # 模拟 Worker 续跑后 Provider 为同一语义调用生成了新的 call_id。
    resumed_call = ToolCall(
        call_id="overwrite-after-resume",
        name="file_write",
        arguments={"path": "report.md", "content": "新内容", "overwrite": True},
    )
    result = await executor.execute(resumed_call)

    assert approval.status is ApprovalStatus.APPROVED
    assert result.status is ToolResultStatus.SUCCESS
    assert (sandbox.root / "report.md").read_text(encoding="utf-8") == "新内容"
    async with database.session_factory() as db_session:
        stored = await db_session.get(ToolApprovalRecord, approval.id)
        resumed_record = await db_session.scalar(
            select(ToolCallRecord).where(
                ToolCallRecord.provider_call_id == "overwrite-after-resume"
            )
        )
    assert stored is not None and stored.decided_at is not None
    assert resumed_record is not None and resumed_record.status is ToolCallStatus.SUCCEEDED
    await database.dispose()


@pytest.mark.asyncio
async def test_ask_user_response_survives_a_new_provider_call_id(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'ask-user.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    service = TaskService(database.session_factory)
    session = await service.create_session("询问用户测试")
    aggregate = await service.create_task(
        session_id=session.id, goal="询问格式", provider="mock", model="mock-model"
    )
    executor = ToolExecutor(
        ToolRegistry([AskUserTool()]),
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
    first_call = ToolCall(
        call_id="ask-before-pause",
        name="ask_user",
        arguments={"question": "报告使用 Markdown 吗？"},
    )

    with pytest.raises(ApprovalRequiredError) as raised:
        await executor.execute(first_call)
    await ApprovalService(database.session_factory).decide(
        raised.value.approval_id,
        approved=True,
        response="是，请使用 Markdown。",
    )
    resumed_call = ToolCall(
        call_id="ask-after-resume",
        name="ask_user",
        arguments={"question": "报告使用 Markdown 吗？"},
    )

    result = await executor.execute(resumed_call)

    assert result.status is ToolResultStatus.SUCCESS
    assert result.content == "是，请使用 Markdown。"
    await database.dispose()
