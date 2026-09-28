"""M1 项目登记/选择/撤销与 Task 绑定的集成测试（实施计划 §6 P-01/P-02）。

覆盖验收要求：

- 登记后被撤销的根不能承载新 Task（`create_task` 直接拒绝）；
- 运行中的 Task 靠**冻结的授权版本**判定：撤销后下一次工具调用被拒绝；
- 切换会话项目不影响已创建 Task 的绑定；
- 撤销会写审计事件，且授权版本自增。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from evoagent.api.app import create_app
from evoagent.config import Settings
from evoagent.core.events import InMemoryEventSink
from evoagent.core.models import ToolCall
from evoagent.db.base import Base
from evoagent.db.models import (
    DEFAULT_WORKSPACE_ID,
    ProjectEventRecord,
    ProjectRecord,
    RunEventRecord,
    RunRecord,
    TaskRecord,
    ToolApprovalRecord,
    ToolCallRecord,
    TurnRecord,
)
from evoagent.db.session import Database
from evoagent.projects.schema import ProjectAuthorization, ProjectAuthorizationRevoked
from evoagent.projects.service import ProjectService, resolve_active_project
from evoagent.tasks.service import TaskService
from evoagent.tools.builtin.list_dir import ListDirTool
from evoagent.tools.effects import PersistentToolMiddleware
from evoagent.tools.executor import ToolExecutor
from evoagent.tools.policy import PermissionPolicy
from evoagent.tools.registry import ToolRegistry


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
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client, database
    await database.dispose()


def make_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("# Repo\n", encoding="utf-8")
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "app.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    return root


@pytest.mark.asyncio
async def test_edit_approval_preview_recomputes_diff_without_writing(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    target = repo / "src" / "app.py"
    original = target.read_text(encoding="utf-8")
    async with api_client(tmp_path) as (client, database):
        project = await ProjectService(database.session_factory).register(
            path=str(repo), authorization=ProjectAuthorization.READ_WRITE
        )
        tasks = TaskService(database.session_factory)
        chat = await tasks.create_session("编辑预览", project_id=project.id)
        aggregate = await tasks.create_task(
            session_id=chat.id, goal="改返回值", provider="mock", model="mock-model"
        )
        async with database.session_factory() as session:
            turn = TurnRecord(run_id=aggregate.run.id, sequence=1, status="running")
            session.add(turn)
            await session.flush()
            call = ToolCallRecord(
                run_id=aggregate.run.id,
                turn_id=turn.id,
                provider_call_id="edit-preview",
                tool_name="edit_file",
                arguments={
                    "path": "src/app.py",
                    "old_text": "return 0",
                    "replacement": "return 1",
                },
                risk="R1",
            )
            session.add(call)
            await session.flush()
            approval = ToolApprovalRecord(
                task_id=aggregate.task.id,
                tool_call_id=call.id,
                risk="R1",
                reason="核对项目文件差异",
            )
            session.add(approval)
            await session.commit()
            approval_id = approval.id

        preview = await client.get(f"/api/v1/tool-approvals/{approval_id}/preview")
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["file_count"] == 1
        assert body["added_lines"] == 1
        assert body["removed_lines"] == 1
        assert body["files"][0]["path"] == "src/app.py"
        assert "+    return 1" in body["files"][0]["diff"]
        assert target.read_text(encoding="utf-8") == original

        target.write_text("def main():\n    return 2\n", encoding="utf-8")
        changed = await client.get(f"/api/v1/tool-approvals/{approval_id}/preview")
        assert changed.status_code == 409


@pytest.mark.asyncio
async def test_register_list_check_and_revoke_project(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    async with api_client(tmp_path) as (client, _database):
        created = await client.post(
            "/api/v1/projects",
            json={"path": str(repo), "name": "示例仓库", "authorization": "read"},
        )
        assert created.status_code == 201
        body = created.json()
        assert body["name"] == "示例仓库"
        assert body["root"] == repo.resolve().as_posix()
        assert body["authorization"] == "read"
        assert body["status"] == "available"
        assert body["authorization_version"] == 1
        assert body["root_available"] is True

        listed = await client.get("/api/v1/projects")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [body["id"]]

        checked = await client.post(f"/api/v1/projects/{body['id']}/check")
        assert checked.status_code == 200
        assert checked.json()["status"] == "available"

        revoked = await client.post(
            f"/api/v1/projects/{body['id']}/revoke",
            json={"reason": "用户收回授权"},
        )
        assert revoked.status_code == 200
        assert revoked.json()["status"] == "revoked"
        assert revoked.json()["authorization_version"] == 2


@pytest.mark.asyncio
async def test_register_rejects_relative_and_link_roots(tmp_path: Path) -> None:
    async with api_client(tmp_path) as (client, _database):
        relative = await client.post("/api/v1/projects", json={"path": "some/dir"})
        assert relative.status_code == 422

        missing = await client.post("/api/v1/projects", json={"path": str(tmp_path / "missing")})
        assert missing.status_code == 422


@pytest.mark.asyncio
async def test_register_same_root_is_idempotent(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    async with api_client(tmp_path) as (client, database):
        first = await client.post("/api/v1/projects", json={"path": str(repo)})
        second = await client.post("/api/v1/projects", json={"path": str(repo)})

        assert first.status_code == 201 and second.status_code == 201
        assert first.json()["id"] == second.json()["id"]
        async with database.session_factory() as session:
            records = tuple(await session.scalars(select(ProjectRecord)))
        assert len(records) == 1


@pytest.mark.asyncio
async def test_revoked_project_cannot_accept_new_task(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    async with api_client(tmp_path) as (client, database):
        project = (await client.post("/api/v1/projects", json={"path": str(repo)})).json()
        session_response = await client.post(
            "/api/v1/sessions",
            json={"title": "只读项目会话", "project_id": project["id"]},
        )
        assert session_response.status_code == 201
        session_id = UUID(session_response.json()["id"])

        await client.post(f"/api/v1/projects/{project['id']}/revoke", json={"reason": "收回"})

        service = TaskService(database.session_factory)
        with pytest.raises(Exception) as error:
            await service.create_task(
                session_id=session_id,
                goal="读取项目结构",
                provider="mock",
                model="mock-model",
            )
        assert "撤销" in str(error.value)


@pytest.mark.asyncio
async def test_task_freezes_binding_and_revocation_blocks_next_tool_call(
    tmp_path: Path,
) -> None:
    """运行中撤销后，下一次工具调用必须被拒绝，并产生审计事件。"""

    repo = make_repo(tmp_path / "repo")
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'binding.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        tasks = TaskService(database.session_factory)
        projects = ProjectService(database.session_factory)
        project = await projects.register(path=str(repo))

        session = await tasks.create_session("会话", DEFAULT_WORKSPACE_ID, project_id=project.id)
        aggregate = await tasks.create_task(
            session_id=session.id,
            goal="列出项目文件",
            provider="mock",
            model="mock-model",
        )
        assert aggregate.task.project_id == project.id
        assert aggregate.task.project_authorization_version == 1

        # 运行中撤销授权。
        await projects.revoke(project.id, reason="运行中收回")

        async with database.session_factory() as db_session:
            task = await db_session.get(TaskRecord, aggregate.task.id)
            run = await db_session.get(RunRecord, aggregate.run.id)
            with pytest.raises(ProjectAuthorizationRevoked):
                await resolve_active_project(
                    db_session,
                    project_id=task.project_id,
                    expected_authorization_version=task.project_authorization_version,
                )

        sink = InMemoryEventSink(aggregate.run.id)
        middleware = PersistentToolMiddleware(
            task_id=aggregate.task.id,
            run_id=aggregate.run.id,
            session_factory=database.session_factory,
            policy=PermissionPolicy(),
            authorization_check=_guard(database, project.id, 1),
        )
        executor = ToolExecutor(
            ToolRegistry([ListDirTool(repo)]),
            sink,
            timeout_seconds=5,
            max_result_chars=1_000,
            middleware=middleware,
        )
        with pytest.raises(ProjectAuthorizationRevoked):
            await executor.execute(ToolCall(call_id="call-1", name="list_dir", arguments={}))

        async with database.session_factory() as db_session:
            events = tuple(
                await db_session.scalars(
                    select(ProjectEventRecord)
                    .where(ProjectEventRecord.project_id == project.id)
                    .order_by(ProjectEventRecord.sequence)
                )
            )
            run_events = tuple(
                await db_session.scalars(
                    select(RunEventRecord)
                    .where(RunEventRecord.run_id == aggregate.run.id)
                    .order_by(RunEventRecord.sequence)
                )
            )
        assert [item.event_type for item in events] == ["project.registered", "project.revoked"]
        assert events[-1].authorization_version == 2
        # 拒绝必须留下 Run 级审计事件，且事件里带项目与原因。
        revoked_events = [item for item in run_events if item.event_type == "authorization.revoked"]
        assert len(revoked_events) == 1
        assert revoked_events[0].payload["project_id"] == str(project.id)
        assert revoked_events[0].payload["detail"] == "revoked"
        assert run is not None
    finally:
        await database.dispose()


def _guard(database: Database, project_id: UUID, version: int):
    async def check() -> None:
        async with database.session_factory() as session:
            await resolve_active_project(
                session,
                project_id=project_id,
                expected_authorization_version=version,
            )

    return check


@pytest.mark.asyncio
async def test_switching_session_project_does_not_change_running_task_binding(
    tmp_path: Path,
) -> None:
    repo_a = make_repo(tmp_path / "repo-a")
    repo_b = make_repo(tmp_path / "repo-b")
    async with api_client(tmp_path) as (client, database):
        project_a = (await client.post("/api/v1/projects", json={"path": str(repo_a)})).json()
        project_b = (await client.post("/api/v1/projects", json={"path": str(repo_b)})).json()
        session_id = (
            await client.post(
                "/api/v1/sessions",
                json={"title": "切换项目", "project_id": project_a["id"]},
            )
        ).json()["id"]

        service = TaskService(database.session_factory)
        aggregate = await service.create_task(
            session_id=UUID(session_id),
            goal="读取第一个项目",
            provider="mock",
            model="mock-model",
        )
        assert aggregate.task.project_id == UUID(project_a["id"])

        switched = await client.put(
            f"/api/v1/sessions/{session_id}/project",
            json={"project_id": project_b["id"]},
        )
        assert switched.status_code == 200
        assert switched.json()["project_id"] == project_b["id"]

        async with database.session_factory() as db_session:
            stored = await db_session.get(TaskRecord, aggregate.task.id)
        assert stored.project_id == UUID(project_a["id"])
        assert stored.project_authorization_version == 1


@pytest.mark.asyncio
async def test_authorization_level_change_bumps_version_and_blocks_run(
    tmp_path: Path,
) -> None:
    """改级（read → read_write）也会让在跑 Task 的下一次调用被拒绝。"""

    repo = make_repo(tmp_path / "repo")
    async with api_client(tmp_path) as (client, database):
        project = (await client.post("/api/v1/projects", json={"path": str(repo)})).json()
        session_id = (
            await client.post(
                "/api/v1/sessions",
                json={"title": "改级", "project_id": project["id"]},
            )
        ).json()["id"]
        aggregate = await TaskService(database.session_factory).create_task(
            session_id=UUID(session_id),
            goal="读取项目",
            provider="mock",
            model="mock-model",
        )

        upgraded = await client.put(
            f"/api/v1/projects/{project['id']}/authorization",
            json={"authorization": "read_write"},
        )
        assert upgraded.status_code == 200
        assert upgraded.json()["authorization"] == "read_write"
        assert upgraded.json()["authorization_version"] == 2

        async with database.session_factory() as db_session:
            with pytest.raises(ProjectAuthorizationRevoked):
                await resolve_active_project(
                    db_session,
                    project_id=aggregate.task.project_id,
                    expected_authorization_version=(aggregate.task.project_authorization_version),
                )
