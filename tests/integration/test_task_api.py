from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from evoagent.api.app import create_app
from evoagent.config import Settings
from evoagent.db.base import Base
from evoagent.db.models import RunEventRecord, RunRecord, TaskRecord
from evoagent.db.session import Database
from evoagent.tasks.lease import JobLeaseManager
from evoagent.tasks.state_machine import PersistentRunStatus, TaskStatus


@asynccontextmanager
async def api_client(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, Database]]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'api.db'}",
        workspace=tmp_path / "workspace",
    )
    app = create_app(settings, database=database)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client,
    ):
        yield client, database
    await database.dispose()


@pytest.mark.asyncio
async def test_create_task_is_atomic_and_returns_202(tmp_path: Path) -> None:
    async with api_client(tmp_path) as (client, database):
        session_response = await client.post("/api/v1/sessions", json={"title": "学习阶段二"})
        assert session_response.status_code == 201

        response = await client.post(
            "/api/v1/tasks",
            json={
                "session_id": session_response.json()["id"],
                "goal": "解释持久化任务的执行过程",
            },
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "queued"
        assert body["latest_run"]["status"] == "queued"

        async with database.session_factory() as session:
            task_count = await session.scalar(select(func.count()).select_from(TaskRecord))
            run_count = await session.scalar(select(func.count()).select_from(RunRecord))
            events = tuple(
                await session.scalars(select(RunEventRecord).order_by(RunEventRecord.sequence))
            )
        assert task_count == 1
        assert run_count == 1
        assert [(event.sequence, event.event_type) for event in events] == [(1, "task.queued")]


@pytest.mark.asyncio
async def test_pause_resume_and_cancel_task(tmp_path: Path) -> None:
    async with api_client(tmp_path) as (client, database):
        session_response = await client.post("/api/v1/sessions", json={"title": "控制任务"})
        task_response = await client.post(
            "/api/v1/tasks",
            json={"session_id": session_response.json()["id"], "goal": "等待 Worker"},
        )
        task_id = task_response.json()["id"]

        paused = await client.post(f"/api/v1/tasks/{task_id}/pause")
        resumed = await client.post(f"/api/v1/tasks/{task_id}/resume")
        cancelled = await client.post(f"/api/v1/tasks/{task_id}/cancel")

        assert paused.json()["status"] == TaskStatus.PAUSED
        assert resumed.json()["status"] == TaskStatus.QUEUED
        assert cancelled.json()["status"] == TaskStatus.CANCELLED
        assert cancelled.json()["latest_run"]["status"] == PersistentRunStatus.CANCELLED
        async with database.session_factory() as session:
            event_types = tuple(
                await session.scalars(
                    select(RunEventRecord.event_type).order_by(RunEventRecord.sequence)
                )
            )
        assert event_types == (
            "task.queued",
            "task.paused",
            "task.resumed",
            "task.cancelled",
        )


@pytest.mark.asyncio
async def test_api_uses_consistent_errors_and_health_checks(tmp_path: Path) -> None:
    async with api_client(tmp_path) as (client, _database):
        live = await client.get("/health/live")
        ready = await client.get("/health/ready")
        missing = await client.get("/api/v1/tasks/00000000-0000-0000-0000-000000000001")

        assert live.json() == {"status": "ok"}
        assert ready.json() == {"status": "ok"}
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "task_not_found"

        invalid = await client.post("/api/v1/sessions", json={"title": ""})
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_running_task_cancellation_is_observed_by_lease_owner(tmp_path: Path) -> None:
    async with api_client(tmp_path) as (client, database):
        session_response = await client.post("/api/v1/sessions", json={"title": "运行中取消"})
        task_response = await client.post(
            "/api/v1/tasks",
            json={"session_id": session_response.json()["id"], "goal": "稍后取消"},
        )
        task_id = task_response.json()["id"]
        manager = JobLeaseManager(database.session_factory, lease_seconds=30)
        lease = await manager.claim_next("worker-a")
        assert lease is not None

        response = await client.post(f"/api/v1/tasks/{task_id}/cancel")

        assert response.status_code == 200
        assert response.json()["status"] == "running"
        assert response.json()["cancel_requested"] is True
        assert await manager.cancellation_requested(lease) is True
