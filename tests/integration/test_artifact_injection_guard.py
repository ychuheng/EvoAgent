"""注入门禁（S0b / M-A0）：授权 → hash → 当前策略复查 → 复用或隔离。

注意 fixture 的构造方式：**不能用 `ToolOutputStore.preserve()` 造敏感样本**——它自己
就会先脱敏。这里用 `ArtifactService.create_unique()` 直接写入明文，模拟"旧规则或旧
writer 写下的归档"，正是本门禁要拦的场景。

M-A0 第二步（加列 + 迁移）之后，检查结果可以持久化：`verified` + 当前策略版本 +
当前 hash 三者齐备才复用，否则重新复查。跨阶段契约由
`test_check_metadata_contract_holds_before_and_after_migration` 保证同一份测试两阶段都能跑。
"""

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from evoagent.core.context_policy import ContextPolicyError, LegacyContextPolicy
from evoagent.core.models import LoopState, Message, MessageRole, ModelRequest
from evoagent.db.base import Base
from evoagent.db.models import ArtifactRecord, ContextRevisionRecord, RunEventRecord
from evoagent.db.session import Database
from evoagent.privacy.artifact_access import (
    BLOCK_EVENT_TYPE,
    ArtifactCheckUnavailable,
    ArtifactInjectionGuard,
    ArtifactNotInjectable,
    ArtifactSensitiveContent,
)
from evoagent.runtime.context_store import ContextStore
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


def _counting_scanner(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """替换门禁里的复查原语，记录每次真正发生的扫描。"""

    calls: list[str] = []
    from evoagent.privacy import artifact_access
    from evoagent.privacy.redaction import redact_text_result as real

    def counted(text: str):
        calls.append(text[:16])
        return real(text)

    monkeypatch.setattr(artifact_access, "redact_text_result", counted)
    return calls


@pytest.mark.asyncio
async def test_verified_artifact_is_reused_without_rescan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """复用条件齐备时不得再扫一遍——这是 M-A0 的收益本身。"""

    database, aggregate, artifacts, _store, guard = await _environment(tmp_path)
    record = await _add_artifact(artifacts, aggregate, CLEAN_TEXT)
    await guard.mark_verified(
        artifact_id=record.id, run_id=aggregate.run.id, source_hash=record.content_hash
    )
    calls = _counting_scanner(monkeypatch)

    text = await guard.read_verified_text(
        artifact_id=record.id, run_id=aggregate.run.id, purpose="artifact_read"
    )

    assert text == CLEAN_TEXT
    assert calls == []
    await database.dispose()


@pytest.mark.asyncio
async def test_stale_policy_version_forces_rescan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """策略升版后旧检查结论作废：必须重新复查，不能沿用。"""

    from evoagent.privacy.redaction import POLICY_VERSION

    database, aggregate, artifacts, _store, guard = await _environment(tmp_path)
    record = await _add_artifact(artifacts, aggregate, CLEAN_TEXT)
    await guard.mark_verified(
        artifact_id=record.id, run_id=aggregate.run.id, source_hash=record.content_hash
    )
    async with database.session_factory() as session:
        stored = await session.get(ArtifactRecord, record.id)
        stored.redaction_policy_version = POLICY_VERSION - 1
        await session.commit()
    calls = _counting_scanner(monkeypatch)

    await guard.read_verified_text(
        artifact_id=record.id, run_id=aggregate.run.id, purpose="artifact_read"
    )

    assert len(calls) == 1
    await database.dispose()


@pytest.mark.asyncio
async def test_changed_checked_hash_forces_rescan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """检查 hash 与当前正文不一致时同样作废检查结论。"""

    database, aggregate, artifacts, _store, guard = await _environment(tmp_path)
    record = await _add_artifact(artifacts, aggregate, CLEAN_TEXT)
    await guard.mark_verified(
        artifact_id=record.id, run_id=aggregate.run.id, source_hash=record.content_hash
    )
    async with database.session_factory() as session:
        stored = await session.get(ArtifactRecord, record.id)
        stored.redaction_checked_hash = "sha256:" + "0" * 64
        await session.commit()
    calls = _counting_scanner(monkeypatch)

    await guard.read_verified_text(
        artifact_id=record.id, run_id=aggregate.run.id, purpose="artifact_read"
    )

    assert len(calls) == 1
    await database.dispose()


@pytest.mark.asyncio
async def test_database_rejects_verified_without_metadata(tmp_path: Path) -> None:
    """库层面的自相矛盾拦截：verified 必须带策略版本与检查 hash。"""

    from sqlalchemy.exc import IntegrityError

    database, aggregate, artifacts, _store, _guard = await _environment(tmp_path)
    async with database.session_factory() as session:
        session.add(
            ArtifactRecord(
                run_id=aggregate.run.id,
                type="tool_output",
                uri="x.txt",
                content_hash="sha256:" + "1" * 64,
                size_bytes=1,
                attributes={},
                redaction_status="verified",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
    await database.dispose()


class _StubLeaseGuard:
    """只提供 `lease.run_id` 与空 `check()`：本测试关注的是上下文修订的错误码翻译。"""

    def __init__(self, run_id) -> None:
        self.lease = SimpleNamespace(run_id=run_id)

    async def check(self, _session) -> None:
        return None


async def _prepare_context_revision(
    database, aggregate, artifact_id, store, *, content: str
) -> str:
    """构造一条指向给定 artifact 的上下文修订，返回其 revision id。"""

    revision_id = uuid4()
    async with database.session_factory() as session:
        session.add(
            ContextRevisionRecord(
                id=revision_id,
                run_id=aggregate.run.id,
                revision=1,
                parent_id=None,
                dedupe_key=f"dedupe-{revision_id.hex}",
                input_hash=f"input-{revision_id.hex}",
                policy_hash=f"policy-{revision_id.hex}",
                artifact_id=artifact_id,
                summary={},
                estimate={},
            )
        )
        await session.commit()
    context_store = ContextStore(
        database.session_factory,
        _StubLeaseGuard(aggregate.run.id),
        store,
        LegacyContextPolicy(),
    )
    state = LoopState(
        schema_version=2,
        messages=(Message(role=MessageRole.USER, content=content),),
        completed_iterations=0,
        usage_is_complete=False,
        config_hash="config-hash",
        context_revision_id=revision_id,
    )
    request = ModelRequest(
        messages=(Message(role=MessageRole.USER, content=content),), model="mock-model"
    )
    await context_store.prepare(state, request)
    return revision_id


@pytest.mark.asyncio
async def test_context_revision_artifact_is_gated_by_current_policy(tmp_path: Path) -> None:
    """context_source 归档同样必须过当前策略，且错误码要点明原因。"""

    database, aggregate, artifacts, store, _guard = await _environment(tmp_path)
    artifact = await _add_artifact(
        artifacts, aggregate, SENSITIVE_TEXT, artifact_type="context_source"
    )

    with pytest.raises(ContextPolicyError) as error:
        await _prepare_context_revision(database, aggregate, artifact.id, store, content="继续任务")
    assert error.value.code == "context_artifact_sensitive"

    async with database.session_factory() as session:
        events = tuple(
            await session.scalars(
                select(RunEventRecord).where(RunEventRecord.event_type == BLOCK_EVENT_TYPE)
            )
        )
    assert [event.payload["purpose"] for event in events] == ["context_revision"]
    await database.dispose()


@pytest.mark.asyncio
async def test_clean_context_revision_artifact_passes(tmp_path: Path) -> None:
    database, aggregate, artifacts, store, _guard = await _environment(tmp_path)
    artifact = await _add_artifact(artifacts, aggregate, CLEAN_TEXT, artifact_type="context_source")

    revision_id = await _prepare_context_revision(
        database, aggregate, artifact.id, store, content="继续任务"
    )

    assert revision_id is not None
    await database.dispose()


@pytest.mark.asyncio
async def test_deleted_context_artifact_reports_corrupt(tmp_path: Path) -> None:
    """既有语义保持不变：artifact 文件被删 → context_artifact_invalid。"""

    database, aggregate, artifacts, store, _guard = await _environment(tmp_path)
    artifact = await _add_artifact(artifacts, aggregate, CLEAN_TEXT, artifact_type="context_source")
    (store._root / artifact.uri).unlink()  # noqa: SLF001 - 直接删磁盘内容以模拟损坏

    with pytest.raises(ContextPolicyError) as error:
        await _prepare_context_revision(database, aggregate, artifact.id, store, content="继续任务")
    assert error.value.code == "context_artifact_invalid"
    await database.dispose()
