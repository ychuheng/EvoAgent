"""M2 编辑与副作用账本衔接的集成测试（实施计划 §7 E-05）。

验证：编辑工具的规范化参数会作为 `ToolEffect` 的语义幂等键；同一批编辑重放（Worker 重试）
不会二次应用，而是直接复用已提交结果。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from evoagent.core.events import InMemoryEventSink
from evoagent.core.models import ToolCall, ToolResultStatus
from evoagent.db.base import Base
from evoagent.db.models import ToolEffectRecord, ToolEffectStatus
from evoagent.db.session import Database
from evoagent.projects.schema import ProjectAuthorization
from evoagent.tasks.service import TaskService
from evoagent.tools.builtin.project_edit import EditFileTool
from evoagent.tools.effects import PersistentToolMiddleware
from evoagent.tools.executor import ToolExecutor
from evoagent.tools.policy import PermissionPolicy
from evoagent.tools.registry import ToolRegistry


@asynccontextmanager
async def editing_context(tmp_path: Path) -> AsyncIterator[tuple[Database, Path, object]]:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "store.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (root / "src" / "cli.py").write_text("from store import add\n", encoding="utf-8")

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'edit.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    service = TaskService(database.session_factory)
    session = await service.create_session("编辑会话")
    aggregate = await service.create_task(
        session_id=session.id, goal="改一个函数名", provider="mock", model="mock-model"
    )
    try:
        yield database, root, aggregate
    finally:
        await database.dispose()


def make_executor(database: Database, aggregate, root: Path) -> ToolExecutor:
    registry = ToolRegistry([EditFileTool(root, authorization=ProjectAuthorization.READ_WRITE)])
    middleware = PersistentToolMiddleware(
        task_id=aggregate.task.id,
        run_id=aggregate.run.id,
        session_factory=database.session_factory,
        policy=PermissionPolicy(),
    )
    return ToolExecutor(
        registry,
        InMemoryEventSink(aggregate.run.id),
        timeout_seconds=10,
        max_result_chars=50_000,
        middleware=middleware,
    )


@pytest.mark.asyncio
async def test_edit_records_committed_effect_and_reuses_it_on_replay(tmp_path: Path) -> None:
    async with editing_context(tmp_path) as (database, root, aggregate):
        executor = make_executor(database, aggregate, root)
        arguments = {
            "path": "src/store.py",
            "old_text": "def add(a, b):",
            "replacement": "def sum_values(a, b):",
        }
        first = await executor.execute(
            ToolCall(call_id="call-1", name="edit_file", arguments=arguments)
        )
        assert first.status is ToolResultStatus.SUCCESS
        assert "applied" in first.content
        target = root / "src" / "store.py"
        assert "def sum_values(a, b):" in target.read_text(encoding="utf-8")
        after_first = target.read_bytes()

        async with database.session_factory() as session:
            effects = tuple(await session.scalars(select(ToolEffectRecord)))
        assert len(effects) == 1
        assert effects[0].status is ToolEffectStatus.COMMITTED
        assert effects[0].effect_scope == str(aggregate.task.id)

        # 重放同一语义调用（Provider 会生成新的 call_id）：必须复用已提交结果，不二次应用。
        replay = await executor.execute(
            ToolCall(call_id="call-2", name="edit_file", arguments=arguments)
        )
        assert replay.status is ToolResultStatus.SUCCESS
        assert target.read_bytes() == after_first
        assert "sum_values" in target.read_text(encoding="utf-8")

        async with database.session_factory() as session:
            effects = tuple(await session.scalars(select(ToolEffectRecord)))
        assert len(effects) == 1


@pytest.mark.asyncio
async def test_conflicting_edit_reports_failure_and_changes_nothing(tmp_path: Path) -> None:
    async with editing_context(tmp_path) as (database, root, aggregate):
        executor = make_executor(database, aggregate, root)
        target = root / "src" / "cli.py"
        before = target.read_bytes()

        result = await executor.execute(
            ToolCall(
                call_id="call-conflict",
                name="edit_file",
                arguments={
                    "path": "src/cli.py",
                    "old_text": "does-not-exist",
                    "replacement": "x",
                },
            )
        )

        assert result.status is ToolResultStatus.ERROR
        assert result.error_code == "edit_conflict"
        assert target.read_bytes() == before

        async with database.session_factory() as session:
            effects = tuple(await session.scalars(select(ToolEffectRecord)))
        # 副作用账本必须留下 UNKNOWN 判定入口，而不是静默丢弃。
        assert [item.status for item in effects] == [ToolEffectStatus.UNKNOWN]


@pytest.mark.asyncio
async def test_read_only_project_has_no_edit_tools_registered(tmp_path: Path) -> None:
    async with editing_context(tmp_path) as (database, root, aggregate):
        registry = ToolRegistry([])
        middleware = PersistentToolMiddleware(
            task_id=aggregate.task.id,
            run_id=aggregate.run.id,
            session_factory=database.session_factory,
            policy=PermissionPolicy(),
        )
        executor = ToolExecutor(
            registry,
            InMemoryEventSink(uuid4()),
            timeout_seconds=5,
            max_result_chars=1_000,
            middleware=middleware,
        )

        result = await executor.execute(
            ToolCall(call_id="call-1", name="edit_file", arguments={"path": "src/store.py"})
        )

        assert result.status is ToolResultStatus.ERROR
        assert result.error_code == "tool_not_found"
