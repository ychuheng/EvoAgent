from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from evoagent.api.app import create_app
from evoagent.config import Settings
from evoagent.db.base import Base
from evoagent.db.session import Database
from evoagent.tasks.lease import JobLeaseManager
from evoagent.workers.bootstrap import ConfiguredTaskHandler
from evoagent.workers.main import JobWorker


@pytest.mark.asyncio
async def test_submitted_task_is_completed_by_independent_worker_components(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'e2e.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'e2e.db'}",
        workspace=tmp_path / "workspace",
        lease_seconds=3,
        heartbeat_seconds=1,
    )
    manager = JobLeaseManager(database.session_factory, lease_seconds=3)
    worker = JobWorker(
        worker_id="worker-e2e",
        lease_manager=manager,
        handler=ConfiguredTaskHandler(settings, database),
        heartbeat_seconds=1,
        poll_seconds=0.01,
    )
    app = create_app(settings, database=database)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        session_response = await client.post("/api/v1/sessions", json={"title": "端到端"})
        assert session_response.status_code == 201
        task_response = await client.post(
            "/api/v1/tasks",
            json={
                "session_id": session_response.json()["id"],
                "goal": "离线搜索并生成 Markdown 报告",
            },
        )
        assert task_response.status_code == 202
        task_id = task_response.json()["id"]
        run_id = task_response.json()["latest_run"]["id"]

        assert await worker.run_once() is True
        completed = await client.get(f"/api/v1/tasks/{task_id}")
        trace = await client.get(f"/api/v1/runs/{run_id}/trace")

    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert trace.status_code == 200
    assert trace.json()["final_answer"] == "离线调研完成，报告已保存为 report.md。"
    assert (settings.artifact_root / str(UUID(run_id)) / "report.md").is_file()
    assert [call["tool_name"] for call in trace.json()["tool_calls"]] == [
        "web_search",
        "file_write",
    ]
    assert len(trace.json()["tool_effects"]) == 1
    assert trace.json()["tool_effects"][0]["status"] == "committed"
    await database.dispose()
