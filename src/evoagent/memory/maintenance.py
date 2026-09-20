"""带 epoch 的持久化维护任务，进程退出后可以重新领取。"""

from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import or_, select, update

from evoagent.db.models import (
    ArtifactRecord,
    ContextRevisionRecord,
    MaintenanceJobRecord,
    MemorySourceRecord,
    MemoryVersionRecord,
    MessageRecord,
    RetrievalBatchRecord,
    RetrievalSelectionRecord,
    RunEventRecord,
    RunMemoryReferenceRecord,
    RunRecord,
    RunSnapshotRecord,
    SessionArchiveRecord,
    ToolCallRecord,
    ToolEffectRecord,
    TurnRecord,
)
from evoagent.memory.archival import archive_input, summarize_archive
from evoagent.memory.schema import MemoryError
from evoagent.tasks.lease_guard import database_now


class MaintenanceWorker:
    def __init__(self, factory, artifact_store, index_service=None):
        self.factory = factory
        self.store = artifact_store
        self.owner = f"maintenance:{uuid4().hex}"
        self.index_service = index_service

    async def claim(self):
        async with self.factory() as session:
            now = await database_now(session)
            job = await session.scalar(
                select(MaintenanceJobRecord)
                .where(
                    or_(
                        MaintenanceJobRecord.status == "pending",
                        (MaintenanceJobRecord.status == "running")
                        & (MaintenanceJobRecord.lease_expires_at <= now),
                    )
                )
                .where(
                    MaintenanceJobRecord.kind.in_(
                        ("archive", "erase", "index_source", "index_rebuild")
                        if self.index_service
                        else ("archive", "erase")
                    )
                )
                .order_by(MaintenanceJobRecord.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            epoch = job.lease_epoch
            changed = await session.execute(
                update(MaintenanceJobRecord)
                .where(
                    MaintenanceJobRecord.id == job.id,
                    MaintenanceJobRecord.lease_epoch == epoch,
                )
                .values(
                    status="running",
                    lease_owner=self.owner,
                    lease_epoch=epoch + 1,
                    lease_expires_at=now + timedelta(seconds=120),
                    attempts=job.attempts + 1,
                )
            )
            if changed.rowcount != 1:
                return None
            await session.commit()
            return job.id, epoch + 1

    async def run_once(self):
        lease = await self.claim()
        if lease is None:
            return False
        try:
            await self.execute(*lease)
        except (OSError, ValueError) as error:
            async with self.factory() as session:
                await session.execute(
                    update(MaintenanceJobRecord)
                    .where(
                        MaintenanceJobRecord.id == lease[0],
                        MaintenanceJobRecord.lease_owner == self.owner,
                        MaintenanceJobRecord.lease_epoch == lease[1],
                        MaintenanceJobRecord.lease_expires_at > await database_now(session),
                    )
                    .values(
                        status="failed",
                        error_code=getattr(error, "code", type(error).__name__),
                        next_attempt_at=await database_now(session) + timedelta(seconds=30),
                    )
                )
                await session.commit()
        return True

    async def execute(self, job_id, epoch):
        async with self.factory() as lookup:
            candidate = await lookup.get(MaintenanceJobRecord, job_id)
            if candidate and candidate.kind.startswith("index_"):
                if self.index_service is None:
                    raise MemoryError("index_worker_not_configured")
                return await self.index_service.execute(job_id, self.owner, epoch)
        async with self.factory() as session:
            job = await session.scalar(
                select(MaintenanceJobRecord)
                .where(
                    MaintenanceJobRecord.id == job_id,
                    MaintenanceJobRecord.status == "running",
                    MaintenanceJobRecord.lease_owner == self.owner,
                    MaintenanceJobRecord.lease_epoch == epoch,
                    MaintenanceJobRecord.lease_expires_at > await database_now(session),
                )
                .with_for_update()
            )
            if job is None:
                raise MemoryError("maintenance_lease_lost")
            if job.kind == "archive":
                session_id = UUID(job.payload["session_id"])
                messages, digest = await archive_input(
                    session, session_id, job.payload["end_sequence"]
                )
                if digest != job.payload["source_hash"]:
                    raise MemoryError("archive_source_changed")
                archive_record = SessionArchiveRecord(
                    session_id=session_id,
                    start_sequence=messages[0].session_sequence,
                    end_sequence=job.payload["end_sequence"],
                    source_hash=digest,
                    summary=summarize_archive(messages),
                    config=job.payload["config"],
                )
                session.add(archive_record)
                await session.flush()
                from evoagent.retrieval.indexing import enqueue_source

                await enqueue_source(session, f"archive:{archive_record.id}")
                job.result = {"source_hash": digest}
            elif job.kind == "erase":
                version = await session.get(MemoryVersionRecord, UUID(job.payload["version_id"]))
                if version is None or version.status not in {"revoked", "erased"}:
                    raise MemoryError("erase_requires_revocation")
                references = tuple(
                    await session.scalars(
                        select(RunMemoryReferenceRecord).where(
                            RunMemoryReferenceRecord.version_id == version.id
                        )
                    )
                )
                affected_runs = {ref.run_id for ref in references}
                source_ids = set(
                    await session.scalars(
                        select(MemorySourceRecord.message_id).where(
                            MemorySourceRecord.version_id == version.id
                        )
                    )
                )
                erased_archives = set()
                while True:
                    messages = tuple(
                        await session.scalars(
                            select(MessageRecord).where(
                                or_(
                                    MessageRecord.id.in_(source_ids),
                                    (MessageRecord.run_id.in_(affected_runs))
                                    & (MessageRecord.kind == "terminal"),
                                )
                            )
                        )
                    )
                    new_keys = []
                    for message in messages:
                        for archive in await session.scalars(
                            select(SessionArchiveRecord).where(
                                SessionArchiveRecord.session_id == message.session_id,
                                SessionArchiveRecord.start_sequence <= message.session_sequence,
                                SessionArchiveRecord.end_sequence >= message.session_sequence,
                            )
                        ):
                            if archive.id in erased_archives:
                                continue
                            erased_archives.add(archive.id)
                            archive.summary, archive.status = None, "erased"
                            key = f"archive:{archive.id}"
                            new_keys.append(key)
                            from evoagent.retrieval.indexing import enqueue_source

                            await enqueue_source(session, key)
                    if not new_keys:
                        break
                    affected_runs.update(
                        await session.scalars(
                            select(RetrievalBatchRecord.run_id)
                            .join(
                                RetrievalSelectionRecord,
                                RetrievalSelectionRecord.batch_id == RetrievalBatchRecord.id,
                            )
                            .where(
                                RetrievalSelectionRecord.source_key.in_(new_keys),
                                RetrievalSelectionRecord.omission_reason.is_(None),
                            )
                        )
                    )
                # 归档可能被后续 Run 使用；按引用闭包一并清理其派生内容。
                for run_id in affected_runs:
                    for selection in await session.scalars(
                        select(RetrievalSelectionRecord)
                        .join(
                            RetrievalBatchRecord,
                            RetrievalSelectionRecord.batch_id == RetrievalBatchRecord.id,
                        )
                        .where(RetrievalBatchRecord.run_id == run_id)
                    ):
                        selection.text = None
                        selection.evidence = {}
                    # 保守地清除已绑定 Run 的派生正文，保留事件序号和状态骨架。
                    run = await session.get(RunRecord, run_id)
                    run.final_answer = None
                    run.error_message = None
                    for event in await session.scalars(
                        select(RunEventRecord).where(RunEventRecord.run_id == run_id)
                    ):
                        event.payload = {"erased": True}
                    for turn in await session.scalars(
                        select(TurnRecord).where(TurnRecord.run_id == run_id)
                    ):
                        turn.request_summary = {"erased": True}
                        turn.response_summary = {"erased": True}
                    for call in await session.scalars(
                        select(ToolCallRecord).where(ToolCallRecord.run_id == run_id)
                    ):
                        call.arguments = {"erased": True}
                        call.result_summary = None
                        for effect in await session.scalars(
                            select(ToolEffectRecord).where(ToolEffectRecord.tool_call_id == call.id)
                        ):
                            effect.result_content = None
                    for message in await session.scalars(
                        select(MessageRecord).where(
                            MessageRecord.run_id == run_id, MessageRecord.kind == "terminal"
                        )
                    ):
                        from evoagent.sessions.service import text_hash

                        message.content = "[erased derived memory content]"
                        message.content_hash = text_hash(message.content)
                        for archive in await session.scalars(
                            select(SessionArchiveRecord).where(
                                SessionArchiveRecord.session_id == message.session_id,
                                SessionArchiveRecord.start_sequence <= message.session_sequence,
                                SessionArchiveRecord.end_sequence >= message.session_sequence,
                            )
                        ):
                            archive.summary = None
                            archive.status = "erased"
                    for snapshot in await session.scalars(
                        select(RunSnapshotRecord).where(RunSnapshotRecord.run_id == run_id)
                    ):
                        snapshot.state = {"erased": True}
                    for revision in await session.scalars(
                        select(ContextRevisionRecord).where(ContextRevisionRecord.run_id == run_id)
                    ):
                        revision.summary = {"erased": True}
                    for artifact in await session.scalars(
                        select(ArtifactRecord).where(
                            ArtifactRecord.run_id == run_id,
                        )
                    ):
                        await self.store.erase(artifact.uri)
                        artifact.attributes = {"erased": True}
                sources = await session.scalars(
                    select(MessageRecord)
                    .join(MemorySourceRecord, MemorySourceRecord.message_id == MessageRecord.id)
                    .where(MemorySourceRecord.version_id == version.id)
                )
                for message in sources:
                    for archive in await session.scalars(
                        select(SessionArchiveRecord).where(
                            SessionArchiveRecord.session_id == message.session_id,
                            SessionArchiveRecord.start_sequence <= message.session_sequence,
                            SessionArchiveRecord.end_sequence >= message.session_sequence,
                        )
                    ):
                        archive.summary = None
                        archive.status = "erased"
                version.content = None
                version.status = "erased"
                job.result = {"version_id": str(version.id), "content_erased": True}
            else:
                raise MemoryError("unknown_maintenance_kind")
            # 文件清理也可能较慢；提交前再次校验，过期结果不得完成任务。
            now = await database_now(session)
            expiry = job.lease_expires_at
            if expiry.tzinfo is None:
                from datetime import UTC

                expiry = expiry.replace(tzinfo=UTC)
            if expiry <= now:
                raise MemoryError("maintenance_lease_lost")
            job.status = "completed"
            job.lease_owner = None
            job.lease_expires_at = None
            await session.commit()
