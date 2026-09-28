"""产物登记与导出契约的单元测试（file_write 衔接、就地更新、路径边界）。

背景：真实 dev 试跑里模型用 `file_write` 写出了报告，但页面上没有任何可下载产物——
因为 `file_write` 只把字节写进 Run 输出目录，没有登记 `artifacts` 记录，
而 F-04 的预览/下载只读取已登记的记录。这组测试钉住修复后的语义。
"""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from evoagent.db.base import Base
from evoagent.db.models import ArtifactRecord
from evoagent.db.session import Database
from evoagent.tools.builtin.file_write import FileWriteArguments, FileWriteTool, content_type_for
from evoagent.tools.sandbox import RunSandbox
from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore


@pytest.fixture
async def artifacts(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'artifacts.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    store = LocalArtifactStore(tmp_path / "store")
    try:
        yield database, ArtifactService(store, database.session_factory), store
    finally:
        await database.dispose()


async def test_file_write_registers_a_downloadable_artifact(artifacts, tmp_path) -> None:
    database, service, store = artifacts
    run_id = uuid4()
    tool = FileWriteTool(RunSandbox(tmp_path / "store", run_id), service)

    payload = json.loads(
        await tool.invoke(FileWriteArguments(path="reports/summary.md", content="# 摘要\n完成\n"))
    )

    assert payload["downloadable"] is True
    assert payload["path"] == "reports/summary.md"
    assert payload["content_hash"].startswith("sha256:")
    async with database.session_factory() as session:
        rows = tuple(await session.scalars(select(ArtifactRecord)))
    assert len(rows) == 1
    record = rows[0]
    assert str(record.id) == payload["artifact_id"]
    assert record.type == "text/markdown"
    # 登记哈希必须与磁盘上的字节一致，否则 F-04 的导出会拒绝（那是防篡改，不是正常路径）。
    on_disk = await store.read(record.uri)
    assert "sha256:" + hashlib.sha256(on_disk).hexdigest() == record.content_hash
    assert on_disk.decode("utf-8") == "# 摘要\n完成\n"


async def test_file_write_overwrite_updates_the_same_artifact_row(artifacts, tmp_path) -> None:
    """同名覆盖写不能让旧记录指向新字节——否则页面上会出现永远下载不了的产物。"""

    database, service, store = artifacts
    run_id = uuid4()
    tool = FileWriteTool(RunSandbox(tmp_path / "store", run_id), service)

    first = json.loads(await tool.invoke(FileWriteArguments(path="notes.txt", content="v1")))
    second = json.loads(
        await tool.invoke(FileWriteArguments(path="notes.txt", content="v2-完成", overwrite=True))
    )

    assert first["artifact_id"] == second["artifact_id"]
    assert first["content_hash"] != second["content_hash"]
    async with database.session_factory() as session:
        rows = tuple(await session.scalars(select(ArtifactRecord)))
    assert len(rows) == 1
    record = rows[0]
    assert record.size_bytes == len("v2-完成".encode())
    assert "sha256:" + hashlib.sha256(await store.read(record.uri)).hexdigest() == (
        record.content_hash
    )
    assert second["content_hash"] == record.content_hash


async def test_file_write_refuses_to_clobber_without_overwrite(artifacts, tmp_path) -> None:
    from evoagent.tools.base import ToolExecutionError

    _database, service, _store = artifacts
    tool = FileWriteTool(RunSandbox(tmp_path / "store", uuid4()), service)
    await tool.invoke(FileWriteArguments(path="notes.txt", content="v1"))

    with pytest.raises(ToolExecutionError):
        await tool.invoke(FileWriteArguments(path="notes.txt", content="v2"))


async def test_sub_paths_are_allowed_but_escapes_are_rejected(artifacts, tmp_path) -> None:
    _database, service, store = artifacts
    run_id = uuid4()

    record = await service.create_or_replace(
        run_id=run_id,
        name="reports/2026/summary.md",
        content="内容".encode(),
        artifact_type="text/markdown",
        attributes={"content_type": "text/markdown"},
    )
    assert record.uri.endswith("reports/2026/summary.md")
    assert await store.read(record.uri) == "内容".encode()

    for bad in ("../escape.md", "/etc/passwd", "reports/../../escape.md", "", ".."):
        with pytest.raises(ValueError):
            await service.create_or_replace(
                run_id=run_id,
                name=bad,
                content=b"x",
                artifact_type="text/plain",
            )


async def test_artifact_content_type_is_inferred_from_suffix() -> None:
    assert content_type_for("reports/summary.md") == "text/markdown"
    assert content_type_for("data.json") == "application/json"
    assert content_type_for("notes.txt") == "text/plain"
    assert content_type_for("weird") == "text/plain"
