"""人工确认、版本冲突和作用域解析的事务边界。"""

from sqlalchemy import func, select, update

from evoagent.db.models import (
    MaintenanceJobRecord,
    MemoryEntryRecord,
    MemoryEventRecord,
    MemorySourceRecord,
    MemoryVersionRecord,
    MessageRecord,
    SessionRecord,
)
from evoagent.memory.lifecycle import next_status
from evoagent.memory.policy import validate_content
from evoagent.memory.repository import source_message, verify_version
from evoagent.memory.schema import MemoryError
from evoagent.sessions.service import text_hash


class MemoryService:
    def __init__(self, session_factory):
        self.factory = session_factory

    async def propose(self, session_id, proposal, *, origin="manual"):
        validate_content(proposal.content)
        async with self.factory() as session:
            # 同 workspace 的首次插入也串行化，避免不存在的 entry 无法锁住。
            scope = await session.get(SessionRecord, session_id)
            if scope is None:
                raise MemoryError("session_not_found")
            from evoagent.db.models import WorkspaceRecord

            await session.scalar(
                select(WorkspaceRecord)
                .where(WorkspaceRecord.id == scope.workspace_id)
                .with_for_update()
            )
            source = await source_message(session, proposal.source_message_id, session_id)
            validate_content(source.content)
            if proposal.content not in source.content:
                raise MemoryError("unsupported_memory_claim")
            scope_key = "workspace" if proposal.scope == "workspace" else str(session_id)
            entry = await session.scalar(
                select(MemoryEntryRecord)
                .where(
                    MemoryEntryRecord.workspace_id == scope.workspace_id,
                    MemoryEntryRecord.scope_key == scope_key,
                    MemoryEntryRecord.fact_key == proposal.fact_key,
                )
                .with_for_update()
            )
            if entry is None:
                entry = MemoryEntryRecord(
                    workspace_id=scope.workspace_id,
                    session_id=None if proposal.scope == "workspace" else session_id,
                    scope_key=scope_key,
                    fact_key=proposal.fact_key,
                )
                session.add(entry)
                await session.flush()
            revision = (
                await session.scalar(
                    select(func.max(MemoryVersionRecord.revision)).where(
                        MemoryVersionRecord.entry_id == entry.id
                    )
                )
            ) or 0
            previous = await session.scalar(
                select(MemoryVersionRecord)
                .where(
                    MemoryVersionRecord.entry_id == entry.id,
                    MemoryVersionRecord.content_hash == text_hash(proposal.content),
                )
                .order_by(MemoryVersionRecord.revision.desc())
                .limit(1)
            )
            if previous is not None and previous.status == "erased":
                raise MemoryError("erased_memory_source")
            if previous is not None and previous.status in {"proposed", "confirmed"}:
                source_exists = await session.scalar(
                    select(MemorySourceRecord.id).where(
                        MemorySourceRecord.version_id == previous.id,
                        MemorySourceRecord.message_id == source.id,
                    )
                )
                if source_exists:
                    return entry, previous
            version = MemoryVersionRecord(
                entry_id=entry.id,
                revision=revision + 1,
                kind=proposal.kind,
                content=proposal.content,
                content_hash=text_hash(proposal.content),
                confidence=0.5,
                confidence_method="verbatim_source_quote_v1",
                expires_at=proposal.expires_at,
                supersedes_version_id=entry.current_version_id,
                origin_type=origin,
            )
            session.add(version)
            await session.flush()
            session.add(
                MemorySourceRecord(
                    version_id=version.id,
                    message_id=source.id,
                    source_hash=source.content_hash,
                    locator=f"message:{source.session_sequence}",
                )
            )
            session.add(
                MemoryEventRecord(
                    entry_id=entry.id,
                    version_id=version.id,
                    action="proposed",
                    actor=origin,
                    reason="source_quote_validated",
                )
            )
            await session.commit()
            return entry, version

    async def decide(self, session_id, version_id, decision):
        async with self.factory() as session:
            version = await session.get(MemoryVersionRecord, version_id)
            scope = await session.get(SessionRecord, session_id)
            if version is None or scope is None:
                raise MemoryError("memory_not_found")
            entry = await session.scalar(
                select(MemoryEntryRecord)
                .where(MemoryEntryRecord.id == version.entry_id)
                .with_for_update()
            )
            if entry.workspace_id != scope.workspace_id or entry.session_id not in (
                None,
                session_id,
            ):
                raise MemoryError("memory_not_found")
            target = next_status(version.status, decision.action)
            changed = await session.execute(
                update(MemoryEntryRecord)
                .where(
                    MemoryEntryRecord.id == entry.id,
                    MemoryEntryRecord.lock_version == decision.expected_lock_version,
                )
                .values(lock_version=MemoryEntryRecord.lock_version + 1)
            )
            if changed.rowcount != 1:
                raise MemoryError("memory_version_conflict")
            if decision.action == "confirm":
                sources = tuple(
                    await session.scalars(
                        select(MemorySourceRecord).where(
                            MemorySourceRecord.version_id == version.id
                        )
                    )
                )
                if not sources:
                    raise MemoryError("invalid_memory_source")
                for source in sources:
                    origin_message = await session.get(MessageRecord, source.message_id)
                    if origin_message is None:
                        raise MemoryError("invalid_memory_source")
                    origin_scope = await session.get(SessionRecord, origin_message.session_id)
                    if origin_scope.workspace_id != scope.workspace_id:
                        raise MemoryError("invalid_memory_source")
                    message = await source_message(session, source.message_id, origin_scope.id)
                    if (
                        source.source_hash != message.content_hash
                        or not version.content
                        or text_hash(version.content) != version.content_hash
                        or version.content not in message.content
                    ):
                        raise MemoryError("invalid_memory_source")
                if version.supersedes_version_id != entry.current_version_id:
                    raise MemoryError("memory_version_conflict")
                if entry.current_version_id:
                    old = await session.get(MemoryVersionRecord, entry.current_version_id)
                    old.status = "superseded"
                entry.current_version_id = version.id
                entry.status = "confirmed"
            elif entry.current_version_id == version.id:
                entry.current_version_id = None
                entry.status = "revoked"
            version.status = target
            from evoagent.retrieval.indexing import enqueue_source

            await enqueue_source(session, f"memory:{version.id}")
            if decision.action == "confirm" and version.supersedes_version_id:
                await enqueue_source(session, f"memory:{version.supersedes_version_id}")
            if decision.action == "erase":
                # 撤销先提交，离线物理清理随后可重试。
                key = f"erase:{version.id}"
                job = await session.scalar(
                    select(MaintenanceJobRecord).where(MaintenanceJobRecord.dedupe_key == key)
                )
                if job is None:
                    session.add(
                        MaintenanceJobRecord(
                            dedupe_key=key, kind="erase", payload={"version_id": str(version.id)}
                        )
                    )
            session.add(
                MemoryEventRecord(
                    entry_id=entry.id,
                    version_id=version.id,
                    action=decision.action,
                    actor="human",
                    reason="explicit_api_decision",
                )
            )
            await session.commit()
            return entry, version

    async def list(self, session_id, *, query=None):
        async with self.factory() as session:
            scope = await session.get(SessionRecord, session_id)
            if scope is None:
                raise MemoryError("session_not_found")
            entries = tuple(
                await session.scalars(
                    select(MemoryEntryRecord)
                    .where(
                        MemoryEntryRecord.workspace_id == scope.workspace_id,
                        MemoryEntryRecord.scope_key.in_(("workspace", str(session_id))),
                    )
                    .order_by(MemoryEntryRecord.created_at)
                )
            )
            result = []
            for entry in entries:
                versions = await session.scalars(
                    select(MemoryVersionRecord)
                    .where(MemoryVersionRecord.entry_id == entry.id)
                    .order_by(MemoryVersionRecord.revision)
                )
                for version in versions:
                    if query is not None:
                        try:
                            await verify_version(session, version, session_id)
                        except MemoryError:
                            continue
                        terms = query.casefold().split()
                        if terms and not any(term in version.content.casefold() for term in terms):
                            continue
                    result.append((entry, version))
            return result
