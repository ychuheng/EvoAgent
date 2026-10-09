import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from evoagent.api.app import create_app
from evoagent.config import Settings
from evoagent.db.base import Base
from evoagent.db.session import Database
from evoagent.tasks.lease import JobLeaseManager, TaskExecutionResult
from evoagent.tasks.service import TaskService
from evoagent.tasks.state_machine import PersistentRunStatus


@asynccontextmanager
async def client_with_completed_task(
    tmp_path: Path,
    *,
    shared_notifications: bool = False,
) -> AsyncIterator[tuple[AsyncClient, str, str]]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'sse.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'sse.db'}",
        workspace=tmp_path / "workspace",
        sse_poll_seconds=0.01,
        sse_heartbeat_seconds=0.02,
        runtime_shared_notifications_enabled=shared_notifications,
    )
    service = TaskService(database.session_factory)
    session = await service.create_session("SSE 测试")
    aggregate = await service.create_task(
        session_id=session.id, goal="完成任务", provider="mock", model="mock-model"
    )
    manager = JobLeaseManager(database.session_factory, lease_seconds=30)
    lease = await manager.claim_next("worker-sse")
    assert lease is not None
    await manager.finalize(
        lease,
        TaskExecutionResult(status=PersistentRunStatus.COMPLETED, final_answer="完成"),
    )
    app = create_app(settings, database=database)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client, str(aggregate.task.id), str(aggregate.run.id)
    await database.dispose()


@pytest.mark.asyncio
async def test_sse_replays_committed_events_and_supports_last_event_id(tmp_path: Path) -> None:
    async with client_with_completed_task(tmp_path) as (client, task_id, _run_id):
        full = await client.get(f"/api/v1/tasks/{task_id}/events")
        resumed = await client.get(
            f"/api/v1/tasks/{task_id}/events", headers={"Last-Event-ID": "2"}
        )

    assert full.status_code == 200
    assert full.headers["content-type"].startswith("text/event-stream")
    assert "id: 1" in full.text
    assert "event: task.queued" in full.text
    assert "id: 3" in full.text
    assert "id: 1" not in resumed.text
    assert "id: 3" in resumed.text


async def test_api_shared_notifications_keep_replay_and_terminal_close(tmp_path: Path):
    async with client_with_completed_task(tmp_path, shared_notifications=True) as (
        client,
        task_id,
        _run_id,
    ):
        full = await asyncio.wait_for(client.get(f"/api/v1/tasks/{task_id}/events"), timeout=2)
        resumed = await client.get(
            f"/api/v1/tasks/{task_id}/events", headers={"Last-Event-ID": "2"}
        )
    assert full.status_code == resumed.status_code == 200
    assert "id: 1" in full.text and "id: 3" in full.text
    assert "id: 1" not in resumed.text and "id: 3" in resumed.text


@pytest.mark.asyncio
async def test_sse_closes_stream_after_authorization_revoked_terminal(tmp_path: Path) -> None:
    """I-01：`authorization_revoked` 是终态，流必须随之关闭而不是一直挂着。"""

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'revoked.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'revoked.db'}",
        workspace=tmp_path / "workspace",
        sse_poll_seconds=0.01,
        sse_heartbeat_seconds=0.02,
    )
    service = TaskService(database.session_factory)
    session = await service.create_session("授权撤销 SSE")
    aggregate = await service.create_task(
        session_id=session.id, goal="读项目", provider="mock", model="mock-model"
    )
    manager = JobLeaseManager(database.session_factory, lease_seconds=30)
    lease = await manager.claim_next("worker-revoked")
    assert lease is not None
    await manager.finalize(
        lease,
        TaskExecutionResult(
            status=PersistentRunStatus.AUTHORIZATION_REVOKED,
            error_code="authorization_revoked",
            error_message="项目授权已被撤销，本次调用被拒绝",
        ),
    )
    app = create_app(settings, database=database)
    try:
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
        ):
            response = await asyncio.wait_for(
                client.get(f"/api/v1/tasks/{aggregate.task.id}/events"), timeout=5
            )
    finally:
        await database.dispose()

    assert response.status_code == 200
    # 终态事件必须出现，且流必须关闭（否则 wait_for 会超时）。
    assert "run.authorization_revoked" in response.text
    assert response.text.rstrip().endswith("id: 3")


@pytest.mark.asyncio
async def test_trace_api_and_viewer_are_available(tmp_path: Path) -> None:
    async with client_with_completed_task(tmp_path) as (client, _task_id, run_id):
        trace = await client.get(f"/api/v1/runs/{run_id}/trace")
        viewer = await client.get("/viewer")

    assert trace.status_code == 200
    assert trace.json()["status"] == "completed"
    assert [event["sequence"] for event in trace.json()["events"]] == [1, 2, 3]
    assert trace.json()["artifacts"] == []
    assert viewer.status_code == 200
    assert "EvoAgent Trace Viewer" in viewer.text
