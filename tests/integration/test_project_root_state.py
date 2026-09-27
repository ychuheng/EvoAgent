"""M1 P-02 专项：根目录状态必须区分"挂载缺失 / 已被替换 / 权限不足"（计划 §6 P-02）。

计划要求"明确目录不可用、权限不足、挂载缺失"。只回一个布尔值不够用：
挂载丢失要重新挂载、目录被删要重新登记、权限不足要改权限，处置完全不同。
"""

import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from evoagent.api.app import create_app
from evoagent.config import Settings
from evoagent.db.base import Base
from evoagent.db.session import Database
from evoagent.projects.schema import ProjectAuthorizationError
from evoagent.projects.service import ProjectService, root_status
from evoagent.tasks.service import TaskService


@asynccontextmanager
async def api_client(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, Database]]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'root_state.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'root_state.db'}",
        workspace=tmp_path / "workspace",
    )
    app = create_app(settings, database=database)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client, database
    await database.dispose()


def test_root_status_distinguishes_the_failure_modes(tmp_path: Path) -> None:
    directory = tmp_path / "repo"
    directory.mkdir()
    (directory / "README.md").write_text("# Repo\n", encoding="utf-8")
    file_target = tmp_path / "file.txt"
    file_target.write_text("x", encoding="utf-8")

    assert root_status(str(directory)) == "available"
    # 挂载点不存在或目录已被删除。
    assert root_status(str(tmp_path / "missing")) == "missing"
    # 路径还在，但已经不是目录（被文件替换 / 挂载类型变了）。
    assert root_status(str(file_target)) == "not_a_directory"


def test_root_status_reports_permission_denied(tmp_path: Path, monkeypatch) -> None:
    """权限不足必须与"不存在"区分开：monkeypatch 让列举抛 PermissionError。"""

    import os as os_module

    directory = tmp_path / "locked"
    directory.mkdir()

    def deny(path):
        raise PermissionError(13, "permission denied")

    monkeypatch.setattr(os_module, "scandir", deny)
    assert root_status(str(directory)) == "permission_denied"


def test_root_status_reports_unreadable_for_other_os_errors(tmp_path: Path, monkeypatch) -> None:
    import os as os_module

    directory = tmp_path / "broken"
    directory.mkdir()

    def broken(path):
        raise OSError(5, "i/o error")

    monkeypatch.setattr(os_module, "scandir", broken)
    assert root_status(str(directory)) == "unreadable"


@pytest.mark.asyncio
async def test_missing_root_is_reported_and_blocks_new_tasks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Repo\n", encoding="utf-8")

    async with api_client(tmp_path) as (client, database):
        project = (
            await client.post("/api/v1/projects", json={"path": str(repo), "name": "会消失的根"})
        ).json()
        assert project["root_status"] == "available"
        assert project["root_available"] is True

        # 模拟挂载丢失/目录被删。
        shutil.rmtree(repo)

        listed = (await client.get("/api/v1/projects")).json()[0]
        assert listed["root_available"] is False
        assert listed["root_status"] == "missing"
        # 列表里的 status 仍是库中状态，实测结果单独返回，两者不混为一谈。
        assert listed["status"] == "available"

        checked = await client.post(f"/api/v1/projects/{project['id']}/check")
        assert checked.status_code == 200
        assert checked.json()["status"] == "unavailable"
        assert checked.json()["root_status"] == "missing"

        # 不可用的项目不得承载新 Task。
        session_id = (
            await client.post(
                "/api/v1/sessions", json={"title": "旧根", "project_id": project["id"]}
            )
        ).json()["id"]
        with pytest.raises(ProjectAuthorizationError, match="不可用"):
            await TaskService(database.session_factory).create_task(
                session_id=UUID(session_id),
                goal="读旧根",
                provider="mock",
                model="mock-model",
            )


@pytest.mark.asyncio
async def test_replaced_directory_is_reported_as_not_a_directory(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    async with api_client(tmp_path) as (client, _database):
        project = (await client.post("/api/v1/projects", json={"path": str(repo)})).json()

        repo.rmdir()
        repo.write_text("now a file\n", encoding="utf-8")

        checked = await client.post(f"/api/v1/projects/{project['id']}/check")
        assert checked.json()["root_status"] == "not_a_directory"
        assert checked.json()["status"] == "unavailable"


@pytest.mark.asyncio
async def test_recheck_recovers_when_the_root_comes_back(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    async with api_client(tmp_path) as (client, database):
        project = (await client.post("/api/v1/projects", json={"path": str(repo)})).json()
        repo.rmdir()
        await client.post(f"/api/v1/projects/{project['id']}/check")

        # 目录回来后再检查：状态恢复，且**授权版本不变**（可用性变化不是授权变化）。
        repo.mkdir()
        restored = await client.post(f"/api/v1/projects/{project['id']}/check")
        assert restored.json()["status"] == "available"
        assert restored.json()["root_status"] == "available"
        assert restored.json()["authorization_version"] == project["authorization_version"]

        # 服务层同样能重新解析出根。
        active = await ProjectService(database.session_factory).status_report(UUID(project["id"]))
        assert active[1] is True and active[2] == "available"
