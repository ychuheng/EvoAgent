"""项目任务闭环的持久化验收（M2/M3 的"读→改→跑→失败→修正→相同命令复测"）。

为什么单开一条：`scripts/m2_project_edit.py` 与 `scripts/m3_failure_fix.py` 直接驱动
`ToolExecutor`，**不经过持久化运行时**；而 2026-09-28 真实 dev 试跑暴露的缺陷
（相同 argv 的第二次 `run_command` 被 ToolEffect 账本复用第一次结果）恰恰只在
"账本 + 租约 + 事件"这条路上出现。这条测试用 `JobWorker + PersistentAgentRunner +
脚本化 Provider` 跑完整链路，覆盖：

- 跨文件编辑（`apply_patch` 一次改两个文件，含测试期望值）；
- 相同 argv 的两次真实执行：第一次退出非 0、第二次退出 0，两次都要留下独立账本条目；
- 最终文件内容与工具参数一致（diff 一致性）；
- 事件链（tool.started/tool.completed/run.completed）与账本状态一致。

全程离线（Mock Provider + 本地 fixture 副本），**不碰任何真实仓库**，也不产生付费调用。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select

from evoagent.config import Settings
from evoagent.core.context import ContextBuilder
from evoagent.core.models import (
    FinishReason,
    Message,
    MessageRole,
    ModelResponse,
    ToolCall,
)
from evoagent.db.base import Base
from evoagent.db.models import RunEventRecord, RunRecord, TaskRecord, ToolCallRecord
from evoagent.db.session import Database
from evoagent.projects.schema import ProjectAuthorization
from evoagent.projects.service import ProjectService
from evoagent.providers.mock import MockProvider
from evoagent.runtime.persistent_runner import PersistentAgentRunner
from evoagent.tasks.lease import JobLeaseManager
from evoagent.tasks.service import TaskService
from evoagent.tasks.state_machine import PersistentRunStatus, TaskStatus
from evoagent.tools.builtin.project_command import project_command_tools
from evoagent.tools.builtin.project_edit import project_edit_tools
from evoagent.tools.builtin.project_file_read import ProjectFileReadTool
from evoagent.tools.effects import ToolEffectStatus
from evoagent.tools.registry import ToolRegistry
from evoagent.workers.main import JobWorker

FIXTURE = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "project-dev-notes"
VERIFY_ARGV = ["python", "-m", "pytest", "-q"]
# 项目命令只在 Linux 上真正可执行：Windows 宿主没有 Landlock/seccomp，`run_command` 会**拒绝**
# 执行（这是设计上的 fail-closed，不是缺陷）。所以这条验收在 Windows 上跳过，
# 并在 Linux 容器里实跑（见 docs/evaluations/M2-M3闭环验收-2026-09-28.md）。
requires_linux_commands = pytest.mark.skipif(
    sys.platform == "win32", reason="项目命令需要 Linux Landlock/seccomp 隔离环境"
)
# 注入一个真实的失败：fixture 里测试期望 "saved"，先把实现改成 "saved!"。
INJECTED = ('return "saved"', 'return "saved!"')
CLI_OLD = 'return "saved!"'
CLI_NEW = 'return "saved"'
RENDER_OLD = "f\"{note['title']}: {note['body']}\""
RENDER_NEW = "f\"{note['title']} — {note['body']}\""
TEST_OLD = 'self.assertEqual(main(["--file", str(path), "list"]), "Alpha: first")'
TEST_NEW = 'self.assertEqual(main(["--file", str(path), "list"]), "Alpha — first")'


def tool_call(call_id: str, name: str, arguments: dict) -> ModelResponse:
    return ModelResponse(
        message=Message(
            role=MessageRole.ASSISTANT,
            tool_calls=(ToolCall(call_id=call_id, name=name, arguments=arguments),),
        ),
        finish_reason=FinishReason.TOOL_CALLS,
    )


def answer(content: str) -> ModelResponse:
    return ModelResponse(
        message=Message(role=MessageRole.ASSISTANT, content=content),
        finish_reason=FinishReason.STOP,
    )


def scripted_provider() -> MockProvider:
    return MockProvider(
        [
            # 1) 先跑既有测试：应当失败（退出码非 0）。
            tool_call("cmd-1", "run_command", {"argv": VERIFY_ARGV}),
            # 2) 读实现文件，再修回正确实现。
            tool_call("read-cli", "file_read", {"path": "src/fieldnotes/cli.py"}),
            tool_call(
                "edit-cli",
                "edit_file",
                {"path": "src/fieldnotes/cli.py", "old_text": CLI_OLD, "replacement": CLI_NEW},
            ),
            # 3) 跨文件修改：渲染分隔符 + 对应测试期望值，一次 apply_patch 完成。
            tool_call(
                "patch-cross-file",
                "apply_patch",
                {
                    "edits": [
                        {
                            "path": "src/fieldnotes/render.py",
                            "old_text": RENDER_OLD,
                            "replacement": RENDER_NEW,
                        },
                        {
                            "path": "tests/test_fieldnotes.py",
                            "old_text": TEST_OLD,
                            "replacement": TEST_NEW,
                        },
                    ]
                },
            ),
            # 4) 与第 1 步**完全相同**的 argv 再跑一次：必须真的重跑并返回 0。
            tool_call("cmd-2", "run_command", {"argv": VERIFY_ARGV}),
            answer("已修好实现与渲染格式，测试通过（退出码 1 → 0）。"),
        ]
    )


async def prepare_project(tmp_path: Path) -> Path:
    root = tmp_path / "fieldnotes"
    shutil.copytree(FIXTURE, root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    cli = root / "src/fieldnotes/cli.py"
    cli.write_text(cli.read_text(encoding="utf-8").replace(*INJECTED), encoding="utf-8")
    return root


def database_url(tmp_path: Path, name: str) -> str:
    """配置了真实 PostgreSQL 就用它（容器里只能这样跑），否则退回 sqlite。"""

    return os.getenv("EVOAGENT_TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{tmp_path / name}"


def command_tools(root: Path) -> list:
    """项目命令工具：白名单 + 让 fixture 的测试能 import 到自己的包。

    命令子进程只拿到最小环境（这是产品行为），所以项目自己的 `src` 目录要通过
    `project_command_environment` 显式注入——真实项目同样要这样声明。
    """

    return list(
        project_command_tools(
            root,
            authorization=ProjectAuthorization.READ_WRITE,
            allowlist=("python", "pytest"),
            timeout_seconds=120,
            output_bytes=20_000,
            environment={"PYTHONPATH": "src", "PYTHONDONTWRITEBYTECODE": "1"},
        )
    )


@requires_linux_commands
@pytest.mark.asyncio
async def test_read_fix_rerun_same_command_through_the_persistent_runtime(tmp_path: Path) -> None:
    root = await prepare_project(tmp_path)
    url = database_url(tmp_path, "loop.db")
    database = Database(url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        database_url=url,
        workspace=tmp_path / "workspace",
        lease_seconds=30,
        heartbeat_seconds=1,
        # 命令结果是 JSON 载荷，账本里要留下完整的 return_code/argv，别让它被归档成占位串。
        max_tool_result_chars=60_000,
    )
    project = await ProjectService(database.session_factory).register(
        path=str(root), authorization=ProjectAuthorization.READ_WRITE
    )
    tasks = TaskService(database.session_factory)
    session = await tasks.create_session("闭环验收", project_id=project.id)
    aggregate = await tasks.create_task(
        session_id=session.id,
        goal="跑测试，修好实现与渲染格式，再用同一条命令复测。",
        provider="mock",
        model="mock-model",
        project_id=project.id,
    )

    registry = ToolRegistry(
        [
            *project_edit_tools(root, authorization=ProjectAuthorization.READ_WRITE),
            *command_tools(root),
            ProjectFileReadTool(root),
        ]
    )
    runner = PersistentAgentRunner(
        settings=settings,
        session_factory=database.session_factory,
        context_builder=ContextBuilder(),
        provider=scripted_provider(),
        registry=registry,
    )
    worker = JobWorker(
        worker_id="worker-loop",
        lease_manager=JobLeaseManager(database.session_factory, lease_seconds=30),
        handler=runner,
        heartbeat_seconds=1,
        poll_seconds=0.01,
    )
    assert await worker.run_once() is True

    async with database.session_factory() as db:
        task = await db.get(TaskRecord, aggregate.task.id)
        run = await db.get(RunRecord, aggregate.run.id)
        events = tuple(
            await db.scalars(
                select(RunEventRecord.event_type)
                .where(RunEventRecord.run_id == aggregate.run.id)
                .order_by(RunEventRecord.sequence)
            )
        )
        calls = tuple(
            await db.scalars(
                select(ToolCallRecord)
                .where(ToolCallRecord.run_id == aggregate.run.id)
                .order_by(ToolCallRecord.created_at)
            )
        )

    # 1) 终态与事件链
    assert task is not None and task.status is TaskStatus.COMPLETED
    assert run is not None and run.status is PersistentRunStatus.COMPLETED
    assert events[-1] == "run.completed"
    assert events.count("tool.started") == 5
    assert events.count("tool.completed") == 5

    # 2) 两次相同 argv 的 run_command 各自留下记录，且退出码不同（第二次真的重跑了）
    commands = [call for call in calls if call.tool_name == "run_command"]
    assert len(commands) == 2
    codes = []
    for call in commands:
        payload = json.loads(call.result_summary or "{}")
        codes.append(payload.get("return_code"))
        assert payload.get("argv") == VERIFY_ARGV
        assert payload.get("network") == "isolated"
    assert codes[0] not in (0, None)
    assert codes[1] == 0

    # 3) 账本：两次命令是两条独立条目（不是复用第一次结果）
    from evoagent.db.models import ToolEffectRecord

    async with database.session_factory() as db:
        effects = tuple(
            await db.scalars(
                select(ToolEffectRecord).where(
                    ToolEffectRecord.tool_call_id.in_([call.id for call in commands])
                )
            )
        )
    assert len(effects) == 2
    assert len({effect.semantic_key for effect in effects}) == 2
    assert all(effect.status is ToolEffectStatus.COMMITTED for effect in effects)
    assert {effect.tool_call_id for effect in effects} == {call.id for call in commands}

    # 4) 文件内容与工具参数一致（跨文件编辑真的落盘）
    assert (root / "src/fieldnotes/cli.py").read_text(encoding="utf-8").count(CLI_OLD) == 0
    assert RENDER_NEW in (root / "src/fieldnotes/render.py").read_text(encoding="utf-8")
    assert TEST_NEW in (root / "tests/test_fieldnotes.py").read_text(encoding="utf-8")

    # 5) 收尾：直接跑一次测试确认磁盘状态真的通过（不依赖上面那次工具调用）。
    import subprocess

    completed = subprocess.run(
        ["python", "-m", "pytest", "-q"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONPATH": "src", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert completed.returncode == 0, completed.stdout[-500:]
    await database.dispose()


@requires_linux_commands
@pytest.mark.asyncio
async def test_project_task_trace_exposes_the_same_exit_codes_and_files(tmp_path: Path) -> None:
    """页面上看到的东西必须与磁盘/账本一致：Trace 的工具记录就是上面那些退出码。"""

    root = await prepare_project(tmp_path)
    url = database_url(tmp_path, "trace.db")
    database = Database(url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        database_url=url,
        workspace=tmp_path / "workspace",
        lease_seconds=30,
        heartbeat_seconds=1,
        max_tool_result_chars=60_000,
    )
    project = await ProjectService(database.session_factory).register(
        path=str(root), authorization=ProjectAuthorization.READ_WRITE
    )
    tasks = TaskService(database.session_factory)
    session = await tasks.create_session("Trace 一致性", project_id=project.id)
    aggregate = await tasks.create_task(
        session_id=session.id,
        goal="同一条命令跑两次。",
        provider="mock",
        model="mock-model",
        project_id=project.id,
    )
    registry = ToolRegistry(
        [
            *project_edit_tools(root, authorization=ProjectAuthorization.READ_WRITE),
            *command_tools(root),
        ]
    )
    runner = PersistentAgentRunner(
        settings=settings,
        session_factory=database.session_factory,
        context_builder=ContextBuilder(),
        provider=MockProvider(
            [
                tool_call("cmd-1", "run_command", {"argv": VERIFY_ARGV}),
                tool_call(
                    "edit-cli",
                    "edit_file",
                    {"path": "src/fieldnotes/cli.py", "old_text": CLI_OLD, "replacement": CLI_NEW},
                ),
                tool_call("cmd-2", "run_command", {"argv": VERIFY_ARGV}),
                answer("完成"),
            ]
        ),
        registry=registry,
    )
    worker = JobWorker(
        worker_id="worker-trace",
        lease_manager=JobLeaseManager(database.session_factory, lease_seconds=30),
        handler=runner,
        heartbeat_seconds=1,
        poll_seconds=0.01,
    )
    assert await worker.run_once() is True

    from evoagent.trace.service import TraceService

    trace = await TraceService(database.session_factory).get_run_trace(UUID(str(aggregate.run.id)))
    command_calls = [call for call in trace.tool_calls if call["tool_name"] == "run_command"]
    assert len(command_calls) == 2
    payloads = [json.loads(call["result_summary"] or "{}") for call in command_calls]
    assert payloads[0]["return_code"] not in (0, None)
    assert payloads[1]["return_code"] == 0
    # 两次调用的语义身份不同，页面因此不会把第二次显示成"复用第一次结果"。
    assert command_calls[0]["id"] != command_calls[1]["id"]
    await database.dispose()
