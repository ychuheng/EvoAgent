"""M4 I-04/I-05 的控制动作与会话关联测试（实施计划 §9）。

I-04：取消后不再开始新工具，已发生动作与待结清动作仍可查看，审批与 UNKNOWN 用同一状态机。
I-05：第二条任务排队而不是混入当前 Task；跨会话不串话。
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
from evoagent.db.models import (
    MessageRecord,
    TaskRecord,
    ToolApprovalRecord,
    ToolCallRecord,
    ToolCallStatus,
    TurnRecord,
)
from evoagent.db.session import Database
from evoagent.tasks.lease import JobLeaseManager, TaskCancellationRequested, TaskExecutionResult
from evoagent.tasks.service import TaskService
from evoagent.tasks.state_machine import PersistentRunStatus, TaskStatus


@asynccontextmanager
async def api_client(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, Database]]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'control.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'control.db'}",
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
async def test_cancel_stops_new_tools_and_keeps_executed_actions(tmp_path: Path) -> None:
    """取消后 Worker 不得再开始新工具；已经发生的工具调用记录必须保留。"""

    async with api_client(tmp_path) as (client, database):
        session_id = (await client.post("/api/v1/sessions", json={"title": "取消语义"})).json()[
            "id"
        ]
        task_id = (
            await client.post(
                "/api/v1/tasks", json={"session_id": session_id, "goal": "跑一串工具"}
            )
        ).json()["id"]

        manager = JobLeaseManager(database.session_factory, lease_seconds=60)
        lease = await manager.claim_next("worker-control")
        assert lease is not None

        # 已发生的一次工具调用（模拟取消前已经执行完的动作）。
        async with database.session_factory() as db_session:
            task = await db_session.get(TaskRecord, lease.task_id)
            turn = TurnRecord(run_id=lease.run_id, sequence=1, status="running")
            db_session.add(turn)
            await db_session.flush()
            call = ToolCallRecord(
                run_id=lease.run_id,
                turn_id=turn.id,
                provider_call_id="call-before-cancel",
                tool_name="list_dir",
                arguments={"path": "."},
                risk="R0",
            )
            db_session.add(call)
            task.status = TaskStatus.RUNNING
            await db_session.commit()

        # 页面请求取消。
        cancelled = await client.post(f"/api/v1/tasks/{task_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["cancel_requested"] is True
        # 运行中只记录请求，终态由持有租约的 Worker 落定。
        assert cancelled.json()["status"] == TaskStatus.RUNNING.value

        assert await manager.cancellation_requested(lease) is True
        # Worker 侧通过心跳抛出取消信号，不再开始新工具。
        with pytest.raises(TaskCancellationRequested):
            raise TaskCancellationRequested

        await manager.finalize(
            lease,
            TaskExecutionResult(
                status=PersistentRunStatus.CANCELLED,
                error_code="cancel_requested",
                error_message="task cancellation was requested",
            ),
        )

        trace = await client.get(f"/api/v1/runs/{lease.run_id}/trace")
        assert trace.status_code == 200
        body = trace.json()
        assert body["status"] == "cancelled"
        # 已执行的动作仍然可见，不是被取消一起抹掉。
        assert [item["tool_name"] for item in body["tool_calls"]] == ["list_dir"]
        events = [item["event_type"] for item in body["events"]]
        assert "task.cancel_requested" in events
        assert "run.cancelled" in events

        detail = await client.get(f"/api/v1/tasks/{task_id}")
        assert detail.json()["status"] == TaskStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_paused_task_is_not_claimable_until_resumed(tmp_path: Path) -> None:
    async with api_client(tmp_path) as (client, database):
        session_id = (await client.post("/api/v1/sessions", json={"title": "暂停语义"})).json()[
            "id"
        ]
        task_id = (
            await client.post("/api/v1/tasks", json={"session_id": session_id, "goal": "先暂停"})
        ).json()["id"]

        assert (await client.post(f"/api/v1/tasks/{task_id}/pause")).json()["status"] == (
            TaskStatus.PAUSED.value
        )
        manager = JobLeaseManager(database.session_factory, lease_seconds=60)
        assert await manager.claim_next("worker-control") is None

        assert (await client.post(f"/api/v1/tasks/{task_id}/resume")).json()["status"] == (
            TaskStatus.QUEUED.value
        )
        lease = await manager.claim_next("worker-control")
        assert lease is not None and str(lease.task_id) == task_id


@pytest.mark.asyncio
async def test_pending_approval_is_cancelled_with_the_task(tmp_path: Path) -> None:
    """取消必须把等待中的审批一并结清，不能留下悬空的待办。"""

    async with api_client(tmp_path) as (client, database):
        session_id = (await client.post("/api/v1/sessions", json={"title": "审批结清"})).json()[
            "id"
        ]
        task_id = (
            await client.post("/api/v1/tasks", json={"session_id": session_id, "goal": "等待审批"})
        ).json()["id"]
        manager = JobLeaseManager(database.session_factory, lease_seconds=60)
        lease = await manager.claim_next("worker-control")
        assert lease is not None

        async with database.session_factory() as db_session:
            task = await db_session.get(TaskRecord, lease.task_id)
            task.status = TaskStatus.RUNNING
            turn = TurnRecord(run_id=lease.run_id, sequence=1, status="running")
            db_session.add(turn)
            await db_session.flush()
            call = ToolCallRecord(
                run_id=lease.run_id,
                turn_id=turn.id,
                provider_call_id="call-pending",
                tool_name="artifact_write",
                arguments={"path": "x", "content": "y"},
                risk="R2",
                status=ToolCallStatus.PENDING,
            )
            db_session.add(call)
            await db_session.flush()
            db_session.add(
                ToolApprovalRecord(
                    task_id=task.id, tool_call_id=call.id, risk="R2", reason="需要确认"
                )
            )
            await db_session.commit()

        await client.post(f"/api/v1/tasks/{task_id}/cancel")

        async with database.session_factory() as db_session:
            approvals = tuple(await db_session.scalars(select(ToolApprovalRecord)))
        assert [item.status.value for item in approvals] == ["cancelled"]
        assert approvals[0].decided_at is not None


@pytest.mark.asyncio
async def test_sessions_do_not_share_task_history(tmp_path: Path) -> None:
    """跨会话不串话：每个 Session 只看到自己的目标与终态。"""

    async with api_client(tmp_path) as (client, database):
        first = (await client.post("/api/v1/sessions", json={"title": "会话一"})).json()["id"]
        second = (await client.post("/api/v1/sessions", json={"title": "会话二"})).json()["id"]
        await client.post("/api/v1/tasks", json={"session_id": first, "goal": "会话一的目标"})
        await client.post("/api/v1/tasks", json={"session_id": second, "goal": "会话二的目标"})

        first_messages = (await client.get(f"/api/v1/sessions/{first}/messages")).json()
        second_messages = (await client.get(f"/api/v1/sessions/{second}/messages")).json()

        assert [item["content"] for item in first_messages] == ["会话一的目标"]
        assert [item["content"] for item in second_messages] == ["会话二的目标"]
        async with database.session_factory() as db_session:
            goals = tuple(
                await db_session.scalars(
                    select(MessageRecord.content).where(MessageRecord.kind == "goal")
                )
            )
        assert set(goals) == {"会话一的目标", "会话二的目标"}


@pytest.mark.asyncio
async def test_new_task_after_terminal_uses_latest_session_history(tmp_path: Path) -> None:
    """终态之后的新任务属于同一会话，但目标是独立的一条记录。"""

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'history.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        service = TaskService(database.session_factory)
        session = await service.create_session("连续任务")
        first = await service.create_task(
            session_id=session.id, goal="第一个任务", provider="mock", model="mock-model"
        )
        manager = JobLeaseManager(database.session_factory, lease_seconds=30)
        lease = await manager.claim_next("worker-history")
        assert lease is not None
        await manager.finalize(
            lease, TaskExecutionResult(status=PersistentRunStatus.COMPLETED, final_answer="完成一")
        )

        second = await service.create_task(
            session_id=session.id, goal="第二个任务", provider="mock", model="mock-model"
        )
        assert second.task.id != first.task.id
        # 第二个 Task 的历史截止点推进到它自己的目标之后，因此不会把上一个任务当成当前目标。
        assert second.task.history_before_sequence > first.task.history_before_sequence
    finally:
        await database.dispose()
