"""日常文件任务的端到端验收（M5 F-03/F-05 → F-04 产物）。

链路：`extract_text` 读非 UTF-8 文本与文本型 PDF → `organize_files` 先给计划再执行 →
`file_write` 写出摘要 → 产物登记 → 预览与下载哈希一致。全程离线（Mock Provider +
本地 fixture 副本），失败样本（损坏 PDF、目标已存在）**原样保留**为证据。

`organize_files` 会真的移动文件，所以每个用例都在 `organize-lab` 的临时副本上跑，
不碰仓库里的 fixture。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from evoagent.api.app import create_app
from evoagent.config import Settings
from evoagent.core.context import ContextBuilder
from evoagent.core.models import FinishReason, Message, MessageRole, ModelResponse, ToolCall
from evoagent.db.base import Base
from evoagent.db.models import RunEventRecord, ToolCallRecord
from evoagent.db.session import Database
from evoagent.projects.schema import ProjectAuthorization
from evoagent.projects.service import ProjectService
from evoagent.providers.mock import MockProvider
from evoagent.runtime.persistent_runner import PersistentAgentRunner
from evoagent.tasks.lease import JobLeaseManager
from evoagent.tasks.service import TaskService
from evoagent.tasks.state_machine import TaskStatus
from evoagent.tools.builtin.file_write import FileWriteTool
from evoagent.tools.builtin.project_edit import project_edit_tools
from evoagent.tools.builtin.project_extract import ExtractTextTool
from evoagent.tools.builtin.project_file_read import ProjectFileReadTool
from evoagent.tools.builtin.project_organize import OrganizeFilesTool
from evoagent.tools.registry import ToolRegistry
from evoagent.tools.sandbox import RunSandbox
from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore
from evoagent.workers.main import JobWorker

FIXTURE = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "organize-lab"
SUMMARY = "# 摘要\n\n历史资料需要显式编码。\n"
CORRUPT_CODES = {"pdf_unreadable", "pdf_scanned_or_empty", "pdf_encrypted", "pdf_parser_missing"}


def copy_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "organize-lab"
    shutil.copytree(FIXTURE, root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return root


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
            # 非 UTF-8 文本：必须显式声明编码（自动识别只覆盖 UTF-8 与带 BOM 的 UTF-16/32）。
            tool_call(
                "read-legacy",
                "extract_text",
                {"path": "inbox/legacy.txt", "encoding": "gb18030"},
            ),
            # 文本型 PDF。
            tool_call("read-pdf", "extract_text", {"path": "inbox/summary.pdf"}),
            # 损坏的 PDF：必须结构化拒绝，不能编造内容。
            tool_call("read-corrupt", "extract_text", {"path": "inbox/corrupt.pdf"}),
            # 先出计划（dry_run），再执行；reports/taken.pdf 已存在 → 冲突且不覆盖。
            tool_call(
                "plan",
                "organize_files",
                {
                    "dry_run": True,
                    "rules": [
                        {"source": "inbox/alpha.md", "destination": "notes/alpha.md"},
                        {"source": "inbox/beta.md", "destination": "notes/beta.md"},
                        {"source": "inbox/summary.pdf", "destination": "reports/taken.pdf"},
                    ],
                },
            ),
            tool_call(
                "apply",
                "organize_files",
                {
                    "dry_run": False,
                    "rules": [
                        {"source": "inbox/alpha.md", "destination": "notes/alpha.md"},
                        {"source": "inbox/beta.md", "destination": "notes/beta.md"},
                    ],
                },
            ),
            # 把结论写成可下载产物。
            tool_call(
                "write-summary", "file_write", {"path": "notes/summary.md", "content": SUMMARY}
            ),
            answer("已抽取非 UTF-8 文本与 PDF、整理目录，并把摘要写成 notes/summary.md。"),
        ]
    )


@pytest.mark.asyncio
async def test_file_task_extracts_organizes_and_publishes_a_downloadable_artifact(
    tmp_path: Path,
) -> None:
    root = copy_fixture(tmp_path)
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'files.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'files.db'}",
        workspace=tmp_path / "workspace",
        lease_seconds=30,
        heartbeat_seconds=1,
        max_tool_result_chars=60_000,
    )
    project = await ProjectService(database.session_factory).register(
        path=str(root), authorization=ProjectAuthorization.READ_WRITE
    )
    tasks = TaskService(database.session_factory)
    session = await tasks.create_session("文件任务", project_id=project.id)
    aggregate = await tasks.create_task(
        session_id=session.id,
        goal="读 inbox 里的历史文本与 PDF，整理目录，并输出摘要文件。",
        provider="mock",
        model="mock-model",
        project_id=project.id,
    )
    registry = ToolRegistry(
        [
            ExtractTextTool(root),
            OrganizeFilesTool(root, authorization=ProjectAuthorization.READ_WRITE),
            *project_edit_tools(root, authorization=ProjectAuthorization.READ_WRITE),
            ProjectFileReadTool(root),
            # 与 worker bootstrap 一样的装配：file_write 接上 ArtifactService 才会登记可下载产物。
            FileWriteTool(
                RunSandbox(settings.artifact_root, aggregate.run.id),
                ArtifactService(
                    LocalArtifactStore(settings.artifact_root), database.session_factory
                ),
            ),
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
        worker_id="worker-files",
        lease_manager=JobLeaseManager(database.session_factory, lease_seconds=30),
        handler=runner,
        heartbeat_seconds=1,
        poll_seconds=0.01,
    )
    app = create_app(settings, database=database)
    assert await worker.run_once() is True

    async with database.session_factory() as db:
        events = tuple(
            await db.scalars(
                select(RunEventRecord.event_type)
                .where(RunEventRecord.run_id == aggregate.run.id)
                .order_by(RunEventRecord.sequence)
            )
        )
        calls = {
            call.provider_call_id: call
            for call in await db.scalars(
                select(ToolCallRecord).where(ToolCallRecord.run_id == aggregate.run.id)
            )
        }

    # 1) 任务完成且没有未知副作用
    async with database.session_factory() as db:
        from evoagent.db.models import TaskRecord

        task = await db.get(TaskRecord, aggregate.task.id)
    assert task is not None and task.status is TaskStatus.COMPLETED
    assert events[-1] == "run.completed"
    assert "run.failed" not in set(events)

    # 2) 非 UTF-8 文本与文本型 PDF 都读出真实内容
    legacy = calls["read-legacy"].result_summary or ""
    assert "历史资料" in legacy and "编码" in legacy
    pdf = calls["read-pdf"].result_summary or ""
    assert pdf.strip() != ""

    # 3) 损坏 PDF 被结构化拒绝，且没有编造内容
    corrupt = calls["read-corrupt"]
    assert corrupt.status.value == "failed"
    assert corrupt.error_code in CORRUPT_CODES, corrupt.error_code
    assert "损坏" in (corrupt.result_summary or "") or "无法" in (corrupt.result_summary or "")

    # 4) 整理：先计划（不动文件）→ 再执行；已存在的目标不覆盖
    plan = json.loads(calls["plan"].result_summary or "{}")
    assert calls["plan"].status.value == "succeeded"
    assert plan["dry_run"] is True
    assert plan["plan"]["conflicts"] == 1
    conflict_items = [item for item in plan["plan"]["items"] if item.get("status") == "conflict"]
    assert [item["source"] for item in conflict_items] == ["inbox/summary.pdf"]
    assert plan["plan"]["ready"] == 2  # 计划里两条可执行、一条冲突
    # 注：dry_run 不落盘这条属性由 tests/unit/test_project_organize.py 与 M5 验收（7/7）覆盖；
    # 端到端跑完后文件已被 apply 移动，所以这里不能再回头断言"计划阶段没动"。

    applied = json.loads(calls["apply"].result_summary or "{}")
    assert calls["apply"].status.value == "succeeded"
    assert applied["dry_run"] is False
    assert applied["plan"]["conflicts"] == 0
    assert (root / "notes/alpha.md").is_file()
    assert (root / "notes/beta.md").is_file()
    assert not (root / "inbox/alpha.md").exists()
    assert (root / "reports/taken.pdf").is_file()  # 冲突目标保持原样

    # 5) 摘要登记为可下载产物，且预览/下载与登记哈希一致（网页看到的就是这份内容）
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        trace = (await client.get(f"/api/v1/runs/{aggregate.run.id}/trace")).json()
        artifacts = [item for item in trace["artifacts"] if item["uri"].endswith("summary.md")]
        assert len(artifacts) == 1, trace["artifacts"]
        detail = (await client.get(f"/api/v1/artifacts/{artifacts[0]['id']}")).json()
        download = await client.get(f"/api/v1/artifacts/{artifacts[0]['id']}/download")
    assert detail["preview"] == SUMMARY
    assert download.content.decode("utf-8") == SUMMARY
    assert "sha256:" + hashlib.sha256(download.content).hexdigest() == detail["content_hash"]
    # file_write 写的是 Run 的输出空间（不是项目目录）：产物在 sandbox 里，且已登记。
    sandbox_file = settings.artifact_root / str(aggregate.run.id) / "notes/summary.md"
    assert sandbox_file.read_text(encoding="utf-8") == SUMMARY
    await database.dispose()
