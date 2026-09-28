"""网页可见的产物闭环（实施计划 §10 F-04 的验收路径）。

真实 dev 试跑暴露的问题：模型用 `file_write` 写出了报告，磁盘上有文件，但页面上**没有任何
可下载产物**——因为只有 `artifact_write` 会登记 `artifacts` 记录，而 F-04 的预览/下载只读
已登记的记录。这组端到端测试走完整链路：API 建任务 → Worker 跑离线 Provider → Trace 列出产物
→ 预览 → 下载并核对 SHA-256；同时覆盖损坏内容、越界登记与不存在的产物。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from evoagent.api.app import create_app
from evoagent.config import Settings
from evoagent.db.base import Base
from evoagent.db.models import ArtifactRecord
from evoagent.db.session import Database
from evoagent.tasks.lease import JobLeaseManager
from evoagent.workers.bootstrap import ConfiguredTaskHandler
from evoagent.workers.main import JobWorker


async def run_file_task(tmp_path: Path, *, database: Database):
    """建一个"生成报告"任务并让 Worker 跑完，返回 (client, database, task, trace, settings)。"""

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'artifacts.db'}",
        workspace=tmp_path / "workspace",
        artifact_root=tmp_path / "workspace" / "artifacts",
        lease_seconds=3,
        heartbeat_seconds=1,
    )
    worker = JobWorker(
        worker_id="worker-artifacts",
        lease_manager=JobLeaseManager(database.session_factory, lease_seconds=3),
        handler=ConfiguredTaskHandler(settings, database),
        heartbeat_seconds=1,
        poll_seconds=0.01,
    )
    app = create_app(settings, database=database)
    return settings, worker, app


@pytest.mark.asyncio
async def test_file_task_is_previewable_and_downloadable_with_matching_hash(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'artifacts.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings, worker, app = await run_file_task(tmp_path, database=database)

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        session = (await client.post("/api/v1/sessions", json={"title": "产物闭环"})).json()
        created = await client.post(
            "/api/v1/tasks",
            json={"session_id": session["id"], "goal": "离线搜索并生成 Markdown 报告"},
        )
        assert created.status_code == 202
        task = created.json()
        run_id = task["latest_run"]["id"]
        assert await worker.run_once() is True

        trace = (await client.get(f"/api/v1/runs/{run_id}/trace")).json()
        artifact_id = trace["artifacts"][0]["id"]

        detail = (await client.get(f"/api/v1/artifacts/{artifact_id}")).json()
        assert detail["name"] == "report.md"
        assert detail["content_type"] == "text/markdown"
        assert detail["preview_truncated"] is False
        assert "EvoAgent 离线调研报告" in detail["preview"]
        assert detail["download_url"] == f"/api/v1/artifacts/{artifact_id}/download"
        assert detail["content_hash"] == trace["artifacts"][0]["content_hash"]

        download = await client.get(f"/api/v1/artifacts/{artifact_id}/download")
        assert download.status_code == 200
        assert download.headers["x-content-sha256"] == detail["content_hash"]
        assert "report.md" in download.headers["content-disposition"]
        # 下载到的字节与登记的哈希、以及页面展示的预览必须来自同一份内容。
        assert "sha256:" + hashlib.sha256(download.content).hexdigest() == detail["content_hash"]
        assert detail["preview"] == download.content.decode("utf-8")
        on_disk = settings.artifact_root / str(UUID(run_id)) / "report.md"
        assert on_disk.read_bytes() == download.content
    await database.dispose()


@pytest.mark.asyncio
async def test_tampered_or_escaping_artifact_is_refused(tmp_path: Path) -> None:
    """损坏/被替换的内容与越界登记都必须拒绝导出，而不是发一份对不上哈希的文件。"""

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'artifacts.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    _settings, worker, app = await run_file_task(tmp_path, database=database)

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        session = (await client.post("/api/v1/sessions", json={"title": "产物边界"})).json()
        task = (
            await client.post(
                "/api/v1/tasks",
                json={"session_id": session["id"], "goal": "生成报告"},
            )
        ).json()
        run_id = UUID(task["latest_run"]["id"])
        assert await worker.run_once() is True
        trace = (await client.get(f"/api/v1/runs/{run_id}/trace")).json()
        artifact_id = trace["artifacts"][0]["id"]

        # 1) 不存在的产物：404，而不是"随便找一个文件给你"。
        assert (await client.get(f"/api/v1/artifacts/{uuid4()}")).status_code == 404

        # 2) 存储内容被替换：哈希不一致，预览与下载都拒绝。
        async with database.session_factory() as session_db:
            record = await session_db.scalar(select(ArtifactRecord))
            assert record is not None
            target = tmp_path / "workspace" / "artifacts" / record.uri
        original = target.read_bytes()
        target.write_bytes("# 被替换的内容\n".encode())
        assert (await client.get(f"/api/v1/artifacts/{artifact_id}")).status_code == 409
        assert (await client.get(f"/api/v1/artifacts/{artifact_id}/download")).status_code == 409
        target.write_bytes(original)
        assert (await client.get(f"/api/v1/artifacts/{artifact_id}/download")).status_code == 200

        # 3) 有人把登记记录改成指向产物根之外：Store 必须拒绝读取该路径。
        async with database.session_factory() as session_db:
            record = await session_db.scalar(select(ArtifactRecord))
            record.uri = "../../outside.txt"
            await session_db.commit()
        escaped = await client.get(f"/api/v1/artifacts/{artifact_id}/download")
        assert escaped.status_code == 409
        assert "不一致" in escaped.json()["detail"]

        # 4) 产物出口不接受任意路径参数：只有"已登记的 id"这一个入口。
        assert (await client.get("/api/v1/artifacts/../../etc/passwd")).status_code in {404, 422}
    await database.dispose()


@pytest.mark.asyncio
async def test_replaced_frozen_input_fails_before_writing_any_artifact(tmp_path: Path) -> None:
    """输入中途变化：任务必须以 `input_changed` 收场，而不是拿改过的内容继续产出产物。"""

    project_root = tmp_path / "project"
    project_root.mkdir()
    source = project_root / "notes.txt"
    source.write_text("第一版输入\n", encoding="utf-8")

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'artifacts.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    _settings, worker, app = await run_file_task(tmp_path, database=database)

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        project = (
            await client.post(
                "/api/v1/projects",
                json={"path": str(project_root), "name": "输入冻结", "authorization": "read_write"},
            )
        ).json()
        session = (
            await client.post(
                "/api/v1/sessions", json={"title": "输入变化", "project_id": project["id"]}
            )
        ).json()
        created = await client.post(
            "/api/v1/tasks",
            json={
                "session_id": session["id"],
                "goal": "把 notes.txt 整理成报告",
                "project_id": project["id"],
                "input_paths": ["notes.txt"],
            },
        )
        assert created.status_code == 202, created.text
        task = created.json()
        assert task["frozen_inputs"]["files"][0]["sha256"]

        # 冻结之后、Worker 开工之前把文件换掉。
        source.write_text("第二版输入（未授权替换）\n", encoding="utf-8")
        assert await worker.run_once() is True

        current = (await client.get(f"/api/v1/tasks/{task['id']}")).json()
        trace = (await client.get(f"/api/v1/runs/{task['latest_run']['id']}/trace")).json()

    assert current["status"] == "failed"
    assert trace["error_code"] == "input_changed"
    assert trace["artifacts"] == []
    assert [call["tool_name"] for call in trace["tool_calls"]] == []
    assert not list((tmp_path / "workspace" / "artifacts").rglob("*.md"))
    await database.dispose()
