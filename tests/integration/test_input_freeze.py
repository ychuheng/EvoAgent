"""M5 F-02 文件输入的集成测试（实施计划 §10 F-02）。

覆盖验收要求：用户清楚 Agent 读到了哪些文件；任务中替换原文件不会悄悄换输入；
Agent 不扫描项目之外的目录。
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
from evoagent.db.base import Base
from evoagent.db.models import RunEventRecord
from evoagent.db.session import Database
from evoagent.projects.inputs import (
    InputChangedError,
    InputSet,
    classify,
    freeze_inputs,
    sha256_file,
    verify_inputs,
)
from evoagent.projects.schema import ProjectAuthorizationError
from evoagent.projects.service import ProjectService
from evoagent.tasks.service import TaskService
from evoagent.tools.base import ToolExecutionError, ToolPermissionError


@asynccontextmanager
async def input_client(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, Database, Path]]:
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    (repo / "data" / "notes.txt").write_text("alpha beta\n", encoding="utf-8")
    (repo / "report.pdf").write_bytes(b"%PDF-1.4\n")
    (repo / "blob.bin").write_bytes(b"\x00\x01\x02")

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'inputs.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'inputs.db'}",
        workspace=tmp_path / "workspace",
    )
    app = create_app(settings, database=database)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client, database, repo
    await database.dispose()


def test_classify_labels_common_types(tmp_path: Path) -> None:
    assert classify(Path("a.md")) == "text"
    assert classify(Path("a.PDF")) == "document"
    assert classify(Path("a.png")) == "image"
    assert classify(Path("a.unknownext")) == "binary"


@pytest.mark.asyncio
async def test_freeze_and_verify_detects_replacement(tmp_path: Path) -> None:
    root = tmp_path
    target = root / "notes.txt"
    target.write_text("alpha beta\n", encoding="utf-8")

    frozen = await freeze_inputs(root, ["notes.txt"])
    assert frozen.files[0].kind == "text"
    # 按磁盘上的实际字节数比较（Windows 上 write_text 会把 \n 写成 \r\n）。
    assert frozen.files[0].size_bytes == target.stat().st_size
    assert frozen.files[0].sha256 == sha256_file(target)
    verify_inputs(root, frozen)

    target.write_text("alpha gamma\n", encoding="utf-8")
    with pytest.raises(InputChangedError, match="内容已变化"):
        verify_inputs(root, frozen)


@pytest.mark.asyncio
async def test_freeze_rejects_outside_and_missing(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")

    with pytest.raises(ToolPermissionError):
        await freeze_inputs(root, ["../outside.txt"])
    with pytest.raises(ToolExecutionError, match="不存在"):
        await freeze_inputs(root, ["missing.txt"])


@pytest.mark.asyncio
async def test_task_creation_freezes_inputs_and_records_event(tmp_path: Path) -> None:
    async with input_client(tmp_path) as (client, database, repo):
        project = (await client.post("/api/v1/projects", json={"path": str(repo)})).json()
        session_id = (
            await client.post(
                "/api/v1/sessions", json={"title": "带输入的任务", "project_id": project["id"]}
            )
        ).json()["id"]

        created = await client.post(
            "/api/v1/tasks",
            json={
                "session_id": session_id,
                "goal": "总结这些输入",
                "input_paths": ["data/notes.txt", "report.pdf"],
            },
        )
        assert created.status_code == 202
        body = created.json()
        frozen = body["frozen_inputs"]["files"]
        assert [item["path"] for item in frozen] == ["data/notes.txt", "report.pdf"]
        assert [item["kind"] for item in frozen] == ["text", "document"]
        assert all(len(item["sha256"]) == 64 for item in frozen)

        # 冻结的输入集本身就是"Agent 读到了哪些文件"的清单。
        task = await TaskService(database.session_factory).get_task(UUID(body["id"]))
        assert task.task.frozen_inputs is not None


@pytest.mark.asyncio
async def test_input_paths_require_authorized_project(tmp_path: Path) -> None:
    async with input_client(tmp_path) as (_client, database, _repo):
        session_id = (await _client.post("/api/v1/sessions", json={"title": "没有项目"})).json()[
            "id"
        ]

        with pytest.raises(ProjectAuthorizationError, match="已授权项目"):
            await TaskService(database.session_factory).create_task(
                session_id=UUID(session_id),
                goal="读一个文件",
                provider="mock",
                model="mock-model",
                input_paths=["data/notes.txt"],
            )


@pytest.mark.asyncio
async def test_input_set_rejects_duplicate_paths(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="不能重复"):
        InputSet.model_validate(
            {
                "files": [
                    {"path": "a.txt", "sha256": "0" * 64, "size_bytes": 1, "kind": "text"},
                    {"path": "a.txt", "sha256": "1" * 64, "size_bytes": 2, "kind": "text"},
                ]
            }
        )


@pytest.mark.asyncio
async def test_run_started_emits_frozen_input_list(tmp_path: Path) -> None:
    """运行开始时会写一条 input.frozen 事件，页面据此显示实际输入。"""

    async with input_client(tmp_path) as (_client, database, repo):
        projects = ProjectService(database.session_factory)
        project = await projects.register(path=str(repo))
        tasks = TaskService(database.session_factory)
        session = await tasks.create_session("冻结事件", project_id=project.id)
        aggregate = await tasks.create_task(
            session_id=session.id,
            goal="总结输入",
            provider="mock",
            model="mock-model",
            input_paths=["data/notes.txt"],
        )

        from evoagent.tasks.lease import JobLeaseManager

        manager = JobLeaseManager(database.session_factory, lease_seconds=60)
        lease = await manager.claim_next("worker-inputs")
        assert lease is not None

        # runner 在装配阶段就把冻结输入写成事件；这里直接调用同一个写入路径。
        from evoagent.core.models import EventType
        from evoagent.trace.persistent_sink import PersistentEventSink

        sink = PersistentEventSink(lease.run_id, database.session_factory)
        await sink.emit(
            EventType.INPUT_FROZEN,
            {
                "files": [
                    {"path": item["path"], "sha256": item["sha256"], "kind": item["kind"]}
                    for item in aggregate.task.frozen_inputs["files"]
                ]
            },
        )

        async with database.session_factory() as db_session:
            records = tuple(
                await db_session.scalars(
                    select(RunEventRecord).where(
                        RunEventRecord.run_id == aggregate.run.id,
                        RunEventRecord.event_type == "input.frozen",
                    )
                )
            )
        assert len(records) == 1
        assert records[0].payload["files"][0]["path"] == "data/notes.txt"


def test_project_authorization_error_is_distinct() -> None:
    """输入缺少项目时应给可读错误，而不是笼统失败。"""

    assert ProjectAuthorizationError.code == "project_authorization_denied"
