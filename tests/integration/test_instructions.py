"""M4 I-03 运行中补充约束的集成测试（实施计划 §9）。

覆盖验收要求：

- 补充指令在**下一个安全边界**注入，不改写已经执行或等待审批的动作；
- 取走即标记，因此同一条指令不会重复注入；
- 终止态 Task 拒绝追加，提示作为下一次任务发送；
- 页面按会话发送下一条任务时是**新 Task**，不会混进当前 Task。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from evoagent.api.app import create_app
from evoagent.config import Settings
from evoagent.db.base import Base
from evoagent.db.models import MessageRecord
from evoagent.db.session import Database
from evoagent.sessions.service import claim_pending_instructions, record_instruction
from evoagent.tasks.lease import JobLeaseManager, TaskExecutionResult
from evoagent.tasks.service import TaskService
from evoagent.tasks.state_machine import PersistentRunStatus


@asynccontextmanager
async def api_client(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, Database]]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'instruction.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'instruction.db'}",
        workspace=tmp_path / "workspace",
    )
    app = create_app(settings, database=database)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client, database
    await database.dispose()


@pytest.mark.asyncio
async def test_instruction_is_claimed_once_and_only_at_a_boundary(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'claim.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        service = TaskService(database.session_factory)
        session = await service.create_session("补充约束")
        aggregate = await service.create_task(
            session_id=session.id, goal="读取项目结构", provider="mock", model="mock-model"
        )

        async with database.session_factory() as db_session:
            first = await record_instruction(
                db_session, task=aggregate.task, content="只改 src/fieldnotes 下的模块"
            )
            await db_session.commit()
            assert first.injected_at is None

        # 第一次取走：拿到内容并标记注入。
        async with database.session_factory() as db_session:
            claimed = await claim_pending_instructions(db_session, task_id=aggregate.task.id)
            await db_session.commit()
        assert claimed == ("只改 src/fieldnotes 下的模块",)

        # 第二次取走：已经注入过，不再返回（不会重复注入）。
        async with database.session_factory() as db_session:
            again = await claim_pending_instructions(db_session, task_id=aggregate.task.id)
        assert again == ()

        async with database.session_factory() as db_session:
            records = tuple(
                await db_session.scalars(
                    select(MessageRecord).where(MessageRecord.kind == "instruction")
                )
            )
        assert len(records) == 1
        assert records[0].injected_at is not None
        # 指令不绑定 Run：它属于 Task 的时间线，不改写任何已执行动作。
        assert records[0].run_id is None
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_instruction_api_rejects_finished_task(tmp_path: Path) -> None:
    async with api_client(tmp_path) as (client, database):
        session = await client.post("/api/v1/sessions", json={"title": "结束的任务"})
        task = await client.post(
            "/api/v1/tasks",
            json={"session_id": session.json()["id"], "goal": "做点事"},
        )
        task_id = task.json()["id"]

        manager = JobLeaseManager(database.session_factory, lease_seconds=30)
        lease = await manager.claim_next("worker-instruction")
        assert lease is not None
        await manager.finalize(
            lease, TaskExecutionResult(status=PersistentRunStatus.COMPLETED, final_answer="完成")
        )

        response = await client.post(
            f"/api/v1/tasks/{task_id}/instructions", json={"content": "再改一处"}
        )
        assert response.status_code == 409
        assert "下一次任务" in response.json()["detail"]


@pytest.mark.asyncio
async def test_instruction_api_records_pending_instruction_for_running_task(
    tmp_path: Path,
) -> None:
    async with api_client(tmp_path) as (client, _database):
        session = await client.post("/api/v1/sessions", json={"title": "运行中的任务"})
        task = await client.post(
            "/api/v1/tasks",
            json={"session_id": session.json()["id"], "goal": "读取项目"},
        )
        task_id = task.json()["id"]

        created = await client.post(
            f"/api/v1/tasks/{task_id}/instructions",
            json={"content": "  只改指定模块  "},
        )

        assert created.status_code == 200
        body = created.json()
        assert body["content"] == "只改指定模块"
        assert body["injected_at"] is None
        assert body["task_id"] == task_id


@pytest.mark.asyncio
async def test_second_message_in_running_session_creates_a_separate_task(tmp_path: Path) -> None:
    """快速连发两条任务应形成两个 Task，不混进同一个 Task。"""

    async with api_client(tmp_path) as (client, database):
        session_id = (await client.post("/api/v1/sessions", json={"title": "连续两条"})).json()[
            "id"
        ]

        first = await client.post(
            "/api/v1/tasks", json={"session_id": session_id, "goal": "第一个任务"}
        )
        second = await client.post(
            "/api/v1/tasks", json={"session_id": session_id, "goal": "第二个任务"}
        )

        assert first.json()["id"] != second.json()["id"]
        async with database.session_factory() as db_session:
            goals = tuple(
                await db_session.scalars(
                    select(MessageRecord.content).where(MessageRecord.kind == "goal")
                )
            )
        assert set(goals) == {"第一个任务", "第二个任务"}


@pytest.mark.asyncio
async def test_instruction_endpoint_404_for_unknown_task(tmp_path: Path) -> None:
    async with api_client(tmp_path) as (client, _database):
        response = await client.post(
            "/api/v1/tasks/00000000-0000-0000-0000-0000000000ff/instructions",
            json={"content": "x"},
        )
        assert response.status_code == 404
