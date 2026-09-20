"""来源校验与发送前再验证，查询操作无写入副作用。"""

from datetime import UTC, datetime

from sqlalchemy import select

from evoagent.db.models import (
    EvalRunRecord,
    MemoryEntryRecord,
    MemorySourceRecord,
    MemoryVersionRecord,
    MessageRecord,
    RunMemoryReferenceRecord,
    RunRecord,
    SessionRecord,
    TaskRecord,
    ToolApprovalRecord,
    ToolCallRecord,
    ToolEffectRecord,
)
from evoagent.memory.schema import MemoryError
from evoagent.sessions.service import text_hash


async def source_message(session, message_id, session_id):
    message = await session.get(MessageRecord, message_id)
    if (
        message is None
        or message.session_id != session_id
        or message.backfill
        or message.role != "user"
        or message.kind != "goal"
        or text_hash(message.content) != message.content_hash
    ):
        raise MemoryError("invalid_memory_source")
    run = await session.get(RunRecord, message.run_id)
    if run is None or run.status.value != "completed":
        raise MemoryError("source_run_not_completed")
    # 所有评测来源均拒绝，覆盖 HOLDOUT，也避免训练/评测串线。
    if await session.scalar(select(EvalRunRecord.id).where(EvalRunRecord.run_id == run.id)):
        raise MemoryError("evaluation_source_forbidden")
    approvals = await session.scalar(
        select(ToolApprovalRecord.id).where(
            ToolApprovalRecord.task_id == message.task_id, ToolApprovalRecord.status == "pending"
        )
    )
    unknown = await session.scalar(
        select(ToolEffectRecord.id)
        .join(ToolCallRecord, ToolEffectRecord.tool_call_id == ToolCallRecord.id)
        .where(ToolCallRecord.run_id == run.id, ToolEffectRecord.status == "unknown")
    )
    if approvals or unknown:
        raise MemoryError("source_execution_unsettled")
    return message


async def verify_version(session, version, session_id):
    scope = await session.get(SessionRecord, session_id)
    entry = await session.get(MemoryEntryRecord, version.entry_id)
    now = datetime.now(UTC)
    expiry = version.expires_at
    if expiry and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    if (
        scope is None
        or entry.workspace_id != scope.workspace_id
        or entry.session_id not in (None, session_id)
        or entry.status != "confirmed"
        or version.status != "confirmed"
        or entry.current_version_id != version.id
        or not version.content
        or text_hash(version.content) != version.content_hash
        or (expiry and expiry <= now)
    ):
        raise MemoryError("context_source_revoked")
    sources = tuple(
        await session.scalars(
            select(MemorySourceRecord).where(MemorySourceRecord.version_id == version.id)
        )
    )
    if not sources:
        raise MemoryError("context_source_revoked")
    for source in sources:
        message = await session.get(MessageRecord, source.message_id)
        # workspace 记忆可在同 workspace 的另一会话读取，但不能越 workspace。
        origin = await session.get(SessionRecord, message.session_id) if message else None
        if origin is None or origin.workspace_id != scope.workspace_id:
            raise MemoryError("context_source_revoked")
        await source_message(session, source.message_id, origin.id)
        if source.source_hash != message.content_hash or version.content not in message.content:
            raise MemoryError("context_source_revoked")


async def check_run_references(session, run_id):
    from uuid import UUID

    from evoagent.db.models import (
        RetrievalBatchRecord,
        RetrievalSelectionRecord,
        SkillRecord,
        SkillVersionRecord,
    )
    from evoagent.retrieval.sources import load_source
    from evoagent.skills.canonical import content_hash

    run = await session.get(RunRecord, run_id)
    if run is None:
        raise MemoryError("context_source_revoked")
    task = await session.get(TaskRecord, run.task_id)
    for ref in await session.scalars(
        select(RunMemoryReferenceRecord).where(RunMemoryReferenceRecord.run_id == run_id)
    ):
        version = await session.get(MemoryVersionRecord, ref.version_id)
        if version is None or version.content_hash != ref.content_hash:
            raise MemoryError("context_source_revoked")
        await verify_version(session, version, task.session_id)
    batch = await session.scalar(
        select(RetrievalBatchRecord).where(RetrievalBatchRecord.run_id == run_id)
    )
    if batch is None:
        return
    for selection in await session.scalars(
        select(RetrievalSelectionRecord).where(
            RetrievalSelectionRecord.batch_id == batch.id,
            RetrievalSelectionRecord.omission_reason.is_(None),
        )
    ):
        if selection.text is None or text_hash(selection.text) != selection.text_hash:
            raise MemoryError("context_source_revoked")
        if selection.source_key.startswith("skill:"):
            version = await session.get(
                SkillVersionRecord, UUID(selection.source_key.split(":")[1])
            )
            skill = await session.get(SkillRecord, version.skill_id) if version else None
            if (
                skill is None
                or skill.status.value != "enabled"
                or content_hash(version.definition) != selection.source_hash
            ):
                raise MemoryError("context_source_revoked")
        elif selection.source_key.startswith("archive:"):
            source = await load_source(
                session,
                selection.source_key,
                scope_id=batch.session_id,
                cutoff=batch.config["history_before_sequence"],
            )
            if source is None or source.source_hash != selection.source_hash:
                raise MemoryError("context_source_revoked")


async def bind_version(session, run_id, version_id):
    """将实际注入的版本绑定到 Run，供每次模型请求前复验。"""
    run = await session.get(RunRecord, run_id)
    task = await session.get(TaskRecord, run.task_id)
    version = await session.get(MemoryVersionRecord, version_id)
    if version is None:
        raise MemoryError("memory_not_found")
    await verify_version(session, version, task.session_id)
    existing = await session.scalar(
        select(RunMemoryReferenceRecord).where(
            RunMemoryReferenceRecord.run_id == run_id,
            RunMemoryReferenceRecord.version_id == version_id,
        )
    )
    if existing is None:
        session.add(
            RunMemoryReferenceRecord(
                run_id=run_id, version_id=version_id, content_hash=version.content_hash
            )
        )
