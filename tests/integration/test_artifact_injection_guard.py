"""注入门禁（S0b / M-A0 第一步）：授权 → hash → 当前策略复查 → 复用或隔离。

注意 fixture 的构造方式：**不能用 `ToolOutputStore.preserve()` 造敏感样本**——它自己
就会先脱敏。这里用 `ArtifactService.create_unique()` 直接写入明文，模拟"旧规则或旧
writer 写下的归档"，正是本门禁要拦的场景。

第一步（不加列、不改 ORM）下检查结果无法持久化，因此每次注入都会复查；这一点由
`test_clean_artifact_is_rescanned_before_columns_exist` 显式钉住，等第二步加列后再改。
"""

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from evoagent.db.base import Base
from evoagent.db.models import ArtifactRecord, RunEventRecord
from evoagent.db.session import Database
from evoagent.privacy.artifact_access import (
    BLOCK_EVENT_TYPE,
    ArtifactCheckUnavailable,
    ArtifactInjectionGuard,
    ArtifactNotInjectable,
    ArtifactSensitiveContent,
)
from evoagent.tasks.service import TaskService
from evoagent.tools.base import ToolExecutionError, ToolPermissionError
from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore

CLEAN_TEXT = "本批次汇总完成：编号字段类型已核对，唯一性检查通过。\n" * 4
SENSITIVE_TEXT = "连接串：postgres://user:secret@db.internal/app\npassword: fake-value\n"


async def _environment(tmp_path: Path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'guard.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    service = TaskService(database.session_factory)
    session = await service.create_session("注入门禁")
    aggregate = await service.create_task(
        session_id=session.id, goal="读取归档", provider="mock", model="mock-model"
    )
    store = LocalArtifactStore(tmp_path / "artifacts")
    artifacts = ArtifactService(store, database.session_factory)
    guard = ArtifactInjectionGuard(session_factory=database.session_factory, artifact_store=store)
    return database, aggregate, artifacts, store, guard


async def _add_artifact(artifacts, aggregate, content: str, *, artifact_type="tool_output"):
    return await artifacts.create_unique(
        run_id=aggregate.run.id,
        name=f"{uuid4().hex}.txt",
        content=content.encode("utf-8"),
        artifact_type=artifact_type,
        attributes={"redacted": False},
    )


@pytest.mark.asyncio
async def test_clean_artifact_is_injectable(tmp_path: Path) -> None:
    database, aggregate, artifacts, _store, guard = await _environment(tmp_path)
    record = await _add_artifact(artifacts, aggregate, CLEAN_TEXT)

    text = await guard.read_verified_text(
        artifact_id=record.id, run_id=aggregate.run.id, purpose="artifact_read"
    )

    assert text == CLEAN_TEXT
    await database.dispose()


@pytest.mark.asyncio
async def test_sensitive_artifact_is_refused_and_recorded_without_body(tmp_path: Path) -> None:
    database, aggregate, artifacts, _store, guard = await _environment(tmp_path)
    record = await _add_artifact(artifacts, aggregate, SENSITIVE_TEXT)

    with pytest.raises(ArtifactSensitiveContent) as error:
        await guard.read_verified_text(
            artifact_id=record.id, run_id=aggregate.run.id, purpose="artifact_read"
        )
    assert error.value.code == "artifact_sensitive_content"

    async with database.session_factory() as session:
        events = tuple(
            await session.scalars(
                select(RunEventRecord).where(RunEventRecord.event_type == BLOCK_EVENT_TYPE)
            )
        )
    assert len(events) == 1
    payload = events[0].payload
    assert payload["artifact_id"] == str(record.id)
    assert payload["content_hash"] == record.content_hash
    assert payload["rule_categories"] == ["credential"]
    assert payload["purpose"] == "artifact_read"
    # 事件只记身份与类别，不得含正文
    assert "fake-value" not in str(payload)
    assert "secret@" not in str(payload)
    await database.dispose()


@pytest.mark.asyncio
async def test_block_event_is_idempotent_per_source_hash_and_policy(tmp_path: Path) -> None:
    database, aggregate, artifacts, _store, guard = await _environment(tmp_path)
    record = await _add_artifact(artifacts, aggregate, SENSITIVE_TEXT)

    for _ in range(3):
        with pytest.raises(ArtifactSensitiveContent):
            await guard.read_verified_text(
                artifact_id=record.id, run_id=aggregate.run.id, purpose="artifact_read"
            )

    async with database.session_factory() as session:
        events = tuple(
            await session.scalars(
                select(RunEventRecord).where(RunEventRecord.event_type == BLOCK_EVENT_TYPE)
            )
        )
    assert len(events) == 1
    await database.dispose()


@pytest.mark.asyncio
async def test_type_outside_allowlist_is_not_injectable(tmp_path: Path) -> None:
    database, aggregate, artifacts, _store, guard = await _environment(tmp_path)
    record = await _add_artifact(
        artifacts,
        aggregate,
        CLEAN_TEXT,
        artifact_type="application/vnd.evoagent.eval-report+json",
    )

    with pytest.raises(ArtifactNotInjectable) as error:
        await guard.read_verified_text(
            artifact_id=record.id, run_id=aggregate.run.id, purpose="artifact_read"
        )
    assert error.value.code == "artifact_not_injectable"
    await database.dispose()


@pytest.mark.asyncio
async def test_tampered_bytes_are_refused_by_hash(tmp_path: Path) -> None:
    database, aggregate, artifacts, store, guard = await _environment(tmp_path)
    record = await _add_artifact(artifacts, aggregate, CLEAN_TEXT)
    target = store._root / record.uri  # noqa: SLF001 - 测试需要直接篡改磁盘内容
    target.write_text("被篡改的正文", encoding="utf-8")

    with pytest.raises(ToolExecutionError, match="hash mismatch"):
        await guard.read_verified_text(
            artifact_id=record.id, run_id=aggregate.run.id, purpose="artifact_read"
        )
    await database.dispose()


@pytest.mark.asyncio
async def test_artifact_from_another_run_is_refused(tmp_path: Path) -> None:
    database, aggregate, artifacts, _store, guard = await _environment(tmp_path)
    record = await _add_artifact(artifacts, aggregate, CLEAN_TEXT)

    with pytest.raises(ToolPermissionError):
        await guard.read_verified_text(
            artifact_id=record.id, run_id=uuid4(), purpose="artifact_read"
        )
    await database.dispose()


@pytest.mark.asyncio
async def test_scan_budget_exceeded_refuses_instead_of_passing(tmp_path: Path) -> None:
    database, aggregate, artifacts, store, _guard = await _environment(tmp_path)
    record = await _add_artifact(artifacts, aggregate, CLEAN_TEXT)
    guard = ArtifactInjectionGuard(
        session_factory=database.session_factory, artifact_store=store, max_scan_bytes=8
    )

    with pytest.raises(ArtifactCheckUnavailable) as error:
        await guard.read_verified_text(
            artifact_id=record.id, run_id=aggregate.run.id, purpose="artifact_read"
        )
    assert error.value.code == "artifact_check_unavailable"

    async with database.session_factory() as session:
        events = tuple(
            await session.scalars(
                select(RunEventRecord).where(RunEventRecord.event_type == BLOCK_EVENT_TYPE)
            )
        )
    assert [event.payload["reason"] for event in events] == ["scan_budget_exceeded"]
    await database.dispose()


@pytest.mark.asyncio
async def test_scan_timeout_refuses_instead_of_passing(tmp_path: Path) -> None:
    database, aggregate, artifacts, store, _guard = await _environment(tmp_path)
    record = await _add_artifact(artifacts, aggregate, CLEAN_TEXT)
    guard = ArtifactInjectionGuard(
        session_factory=database.session_factory, artifact_store=store, scan_budget_ms=0
    )

    with pytest.raises(ArtifactCheckUnavailable):
        await guard.read_verified_text(
            artifact_id=record.id, run_id=aggregate.run.id, purpose="artifact_read"
        )
    await database.dispose()


@pytest.mark.asyncio
async def test_derived_text_is_checked_every_time(tmp_path: Path) -> None:
    database, aggregate, _artifacts, _store, guard = await _environment(tmp_path)
    from evoagent.sessions.service import text_hash

    clean = await guard.verify_derived_text(
        text=CLEAN_TEXT,
        run_id=aggregate.run.id,
        source_id="archive:summary:1",
        source_hash=text_hash(CLEAN_TEXT),
        purpose="memory_archive",
    )
    assert clean == CLEAN_TEXT

    with pytest.raises(ArtifactSensitiveContent):
        await guard.verify_derived_text(
            text=SENSITIVE_TEXT,
            run_id=aggregate.run.id,
            source_id="archive:summary:2",
            source_hash=text_hash(SENSITIVE_TEXT),
            purpose="memory_archive",
        )
    with pytest.raises(ToolExecutionError, match="derived text hash mismatch"):
        await guard.verify_derived_text(
            text=CLEAN_TEXT,
            run_id=aggregate.run.id,
            source_id="archive:summary:3",
            source_hash=text_hash("别的正文"),
            purpose="memory_archive",
        )
    await database.dispose()


@pytest.mark.asyncio
async def test_check_metadata_contract_holds_before_and_after_migration(tmp_path: Path) -> None:
    """跨 M-A0 两个阶段都成立的契约。

    - 加列前（第一步）：标记动作**跳过**而不是报错，注入仍然可用；
    - 加列后（第二步起）：通过检查的正文被记为 verified，附当前策略版本与检查 hash。

    这样同一份测试在迁移前后都能跑，不需要在第二步回头改它。
    """

    database, aggregate, artifacts, _store, guard = await _environment(tmp_path)
    record = await _add_artifact(artifacts, aggregate, CLEAN_TEXT)
    await guard.mark_verified(
        artifact_id=record.id, run_id=aggregate.run.id, source_hash=record.content_hash
    )

    async with database.session_factory() as session:
        stored = await session.get(ArtifactRecord, record.id)
    assert stored is not None
    if "redaction_status" in ArtifactRecord.__table__.columns:
        assert stored.redaction_status == "verified"
        assert stored.redaction_checked_hash == record.content_hash
        assert stored.redaction_policy_version is not None
    else:
        assert not hasattr(stored, "redaction_status")
    # 两种情况下都要保证正文与 hash 未被改动
    assert stored.content_hash == record.content_hash
    await database.dispose()
